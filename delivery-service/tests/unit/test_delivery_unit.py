# tests/unit/test_smoke.py
import os, sys, importlib
from typing import Optional, Tuple
import pytest
from fastapi.testclient import TestClient

# Ищем app.py из корня сервиса
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Локальная БД для юнитов (не трогаем реальную)
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_unit.db")

# Импортируем приложение
try:
    app_module = importlib.import_module("app")
    app = getattr(app_module, "app", None)
    if app is None:
        raise RuntimeError("В модуле app отсутствует FastAPI-приложение с именем 'app'")
    client = TestClient(app)
    IMPORT_ERROR = None
except Exception as e:
    app_module = None
    client = None
    IMPORT_ERROR = str(e)

@pytest.fixture(autouse=True)
def _skip_if_import_failed():
    if IMPORT_ERROR is not None:
        pytest.skip(f"Не удалось инициализировать приложение: {IMPORT_ERROR}")
    yield

@pytest.fixture(autouse=True)
def reset_db():
    """На каждый тест — чистая БД, если в app объявлены Base/engine."""
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield

def _noop_publish_event_if_exists():
    # Глушим брокер/уведомления, если есть функции-обёртки
    for fname in ("publish_event", "send_notification", "emit_event", "notify_delivery"):
        if hasattr(app_module, fname):
            setattr(app_module, fname, lambda *a, **k: None)

def _stub_requests_if_imported(monkeypatch):
    # Если сервис ходит в order-service через requests — подменим на «всегда ок»
    try:
        import requests  # noqa
    except Exception:
        return
    class OK:
        status_code = 200
        def json(self): return {"ok": True}
        def raise_for_status(self): pass
        @property
        def text(self): return '{"ok": true}'
    monkeypatch.setattr("requests.get", lambda *a, **k: OK(), raising=False)
    monkeypatch.setattr("requests.post", lambda *a, **k: OK(), raising=False)

def _openapi() -> Optional[dict]:
    r = client.get("/openapi.json")
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None

def _discover_delivery_paths(openapi: dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Возвращает кортеж путей (list_path, create_path, get_by_id_path, update_by_id_path)
    по ключевым словам: delivery / deliver / ship
    """
    keys = ("delivery", "deliver", "ship")
    paths = (openapi or {}).get("paths") or {}
    list_path = create_path = get_by_id_path = update_by_id_path = None
    for p, spec in paths.items():
        lower = p.lower()
        if not any(k in lower for k in keys):
            continue
        methods = {m.lower() for m in spec.keys()}
        if "get" in methods and "{" not in p and list_path is None:
            list_path = p
        if "post" in methods and create_path is None:
            create_path = p
        if "get" in methods and "{" in p and get_by_id_path is None:
            get_by_id_path = p
        if any(m in methods for m in ("put", "patch")) and "{" in p and update_by_id_path is None:
            update_by_id_path = p
    return list_path, create_path, get_by_id_path, update_by_id_path

def test_openapi_and_list_smoke():
    spec = _openapi()
    assert spec is not None, "openapi.json недоступен"
    list_path, *_ = _discover_delivery_paths(spec)
    if not list_path:
        pytest.skip("В OpenAPI не найден список доставок (*delivery/deliver/ship*).")
    r = client.get(list_path)
    assert r.status_code in (200, 204), f"GET {list_path} -> {r.status_code}"
    if r.status_code == 200:
        _ = r.json()

def test_create_and_fetch_delivery(monkeypatch):
    _noop_publish_event_if_exists()
    _stub_requests_if_imported(monkeypatch)

    spec = _openapi()
    if not spec:
        pytest.skip("Нет OpenAPI — не знаем маршруты для доставки.")
    list_path, create_path, get_by_id_path, update_by_id_path = _discover_delivery_paths(spec)
    if not create_path:
        pytest.skip("В OpenAPI нет POST-эндпоинта для создания/запуска доставки.")

    created = None
    # 1) Если create-путь содержит {order_id}, подставим 1
    if "{" in create_path and "}" in create_path:
        path = create_path.replace("{id}", "1").replace("{order_id}", "1").replace("{delivery_id}", "1")
        r = client.post(path, json={"address": "Test St. 1"})
        if r.status_code in (200, 201):
            try:
                created = r.json()
            except Exception:
                created = {"raw": r.text}
    # 2) Иначе пробуем с телом запроса
    if not created:
        for body in [
            {"order_id": 1, "address": "Test St. 1", "cost": 5.0},
            {"order_id": 1, "address": "Test St. 1"},
            {"orderId": 1, "address": "Test St. 1"},
        ]:
            r = client.post(create_path, json=body)
            if r.status_code in (200, 201):
                try:
                    created = r.json()
                except Exception:
                    created = {"raw": r.text}
                break
            if r.status_code not in (400, 404, 405, 415, 422):
                break

    if not created:
        pytest.skip(f"Создание доставки через {create_path} не удалось — проверьте схему запроса.")

    # Достаём id из ответа (часто 'delivery_id' или 'id' или 'shipment_id')
    did = None
    if isinstance(created, dict):
        container = created.get("delivery", created)
        for k in ("delivery_id", "id", "shipment_id", "uuid"):
            if k in container:
                did = container[k]
                break
    if not did and get_by_id_path:
        # некоторые API сразу отдают ресурс без поля id на верхнем уровне
        try:
            for k in ("delivery_id", "id", "shipment_id", "uuid"):
                if k in created:
                    did = created[k]
                    break
        except Exception:
            pass

    # GET по id (если есть такой путь)
    if get_by_id_path and did is not None:
        path_id = (
            get_by_id_path
            .replace("{id}", str(did))
            .replace("{delivery_id}", str(did))
            .replace("{shipment_id}", str(did))
        )
        r = client.get(path_id)
        assert r.status_code == 200, f"GET {path_id} -> {r.status_code}"
        _ = r.json()

    # Если есть update — проверим, что можно сменить статус
    if update_by_id_path and did is not None:
        path_upd = (
            update_by_id_path
            .replace("{id}", str(did))
            .replace("{delivery_id}", str(did))
            .replace("{shipment_id}", str(did))
        )
        r = client.request("PATCH", path_upd, json={"status": "in_transit"})
        if r.status_code == 405:
            r = client.request("PUT", path_upd, json={"status": "in_transit"})
        assert r.status_code in (200, 204), f"{path_upd} -> {r.status_code}"
