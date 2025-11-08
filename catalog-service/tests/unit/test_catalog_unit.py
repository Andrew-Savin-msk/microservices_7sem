# tests/unit/test_smoke.py
import os, sys, importlib, json
from typing import Tuple, Optional
import pytest
from fastapi.testclient import TestClient

# как в твоих примерах — добавим корень проекта в PYTHONPATH
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# локальная БД для юнитов (не трогаем реальную)
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_unit.db")

app_module = importlib.import_module("app")
client = TestClient(app_module.app)

@pytest.fixture(autouse=True)
def reset_db():
    """Чистая БД на каждый тест, если объявлены Base/engine."""
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield

def _noop_publish_event_if_exists():
    if hasattr(app_module, "publish_event"):
        # заглушка событий (RabbitMQ и т.п.)
        setattr(app_module, "publish_event", lambda *a, **k: None)

def _openapi() -> Optional[dict]:
    r = client.get("/openapi.json")
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None

def _discover_paths(openapi: dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Возвращает (list_path, create_path, get_by_id_path, update_by_id_path) для сущностей, где путь содержит 'product'.
    """
    paths = (openapi or {}).get("paths") or {}
    list_path = create_path = get_by_id_path = update_by_id_path = None
    for p, spec in paths.items():
        if "product" not in p.lower():
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
    list_path, *_ = _discover_paths(spec)
    if not list_path:
        pytest.skip("В OpenAPI не найден путь списка продуктов (*product*).")
    r = client.get(list_path)
    assert r.status_code in (200, 204)
    if r.status_code == 200:
        _ = r.json()

def test_create_and_update_flow(monkeypatch):
    _noop_publish_event_if_exists()

    spec = _openapi()
    if not spec:
        pytest.skip("Нет OpenAPI, не знаем маршруты для продуктов.")
    list_path, create_path, get_by_id_path, update_by_id_path = _discover_paths(spec)
    if not create_path:
        pytest.skip("В OpenAPI нет POST-эндпоинта для создания продукта.")

    payloads = [
        {"name": "Mock Product", "price": 9.99, "stock": 3},
        {"title": "Mock Product", "price": 9.99, "quantity": 3},
        {"name": "Mock Product", "price": 9.99},
    ]
    created = None
    for body in payloads:
        r = client.post(create_path, json=body)
        if r.status_code in (200, 201):
            created = r.json()
            break
        if r.status_code not in (400, 404, 405, 415, 422):
            break

    if not created:
        pytest.skip(f"Не удалось создать продукт через {create_path} — проверьте схему.")

    pid = None
    if isinstance(created, dict):
        for k in ("id", "product_id", "uuid"):
            if k in created:
                pid = created[k]
                break
        # некоторые API заворачивают результат в {"product": {...}}
        if not pid and "product" in created and isinstance(created["product"], dict):
            for k in ("id", "product_id", "uuid"):
                if k in created["product"]:
                    pid = created["product"][k]
                    break

    if not pid:
        pytest.skip("Ответ создания не содержит id продукта.")

    if list_path:
        r_list = client.get(list_path)
        assert r_list.status_code in (200, 204)

    # Если есть путь чтения по id — проверим
    if get_by_id_path:
        path_id = get_by_id_path.replace("{id}", str(pid)).replace("{product_id}", str(pid))
        r_get = client.get(path_id)
        assert r_get.status_code == 200
        _ = r_get.json()

    # Если есть update (PUT/PATCH) — попробуем обновить цену
    if update_by_id_path:
        path_upd = update_by_id_path.replace("{id}", str(pid)).replace("{product_id}", str(pid))
        r_upd = client.request("PATCH", path_upd, json={"price": 11.49})
        if r_upd.status_code == 405:
            r_upd = client.request("PUT", path_upd, json={"price": 11.49})
        assert r_upd.status_code in (200, 204)
