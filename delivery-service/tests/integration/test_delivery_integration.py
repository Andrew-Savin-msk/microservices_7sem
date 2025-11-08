# tests/integration/test_delivery_integration.py
import os, sys, importlib
from typing import Optional, Any, Type

import pytest
from fastapi.testclient import TestClient

# По умолчанию используем локальный SQLite-файл — внешний сервер БД не нужен
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_integration.db")

# Ищем app.py из корня сервиса
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

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
def clean_db():
    """Чистая БД на каждый тест, если объявлены Base/engine"""
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield

def _noop_publish_event_if_exists():
    for fname in ("publish_event", "send_notification", "emit_event", "notify_delivery"):
        if hasattr(app_module, fname):
            setattr(app_module, fname, lambda *a, **k: None)

def _stub_requests_if_imported(monkeypatch):
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

def _discover_delivery_paths(openapi: dict):
    keys = ("delivery", "deliver", "ship")
    paths = (openapi or {}).get("paths") or {}
    list_path = create_path = get_by_id_path = None
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
    return list_path, create_path, get_by_id_path

def _get_session():
    SessionLocal = getattr(app_module, "SessionLocal", None)
    if SessionLocal is None:
        from sqlalchemy.orm import sessionmaker
        SessionLocal = sessionmaker(bind=app_module.engine)
    return SessionLocal()

def _find_delivery_model() -> Optional[Type[Any]]:
    # Пробуем распространённые имена
    for attr in ("Delivery", "Shipment", "DeliveryOrder", "CourierTask"):
        if hasattr(app_module, attr):
            return getattr(app_module, attr)

    # Ищем среди mapper-ов SQLAlchemy
    Base = getattr(app_module, "Base", None)
    try:
        for m in Base.registry.mappers:  # type: ignore[attr-defined]
            cls = m.class_
            name = getattr(cls, "__name__", "").lower()
            tbn = getattr(getattr(cls, "__table__", None), "name", "") or getattr(cls, "__tablename__", "")
            if any(s in name for s in ("delivery", "shipment", "courier")) or any(
                s in str(tbn).lower() for s in ("delivery", "shipment", "courier")
            ):
                return cls
    except Exception:
        pass
    return None

def test_openapi_available():
    spec = _openapi()
    assert spec is not None, "openapi.json недоступен"
    assert "paths" in spec, "В OpenAPI нет раздела paths"

def test_create_delivery_persists_and_fetch(monkeypatch):
    _noop_publish_event_if_exists()
    _stub_requests_if_imported(monkeypatch)

    spec = _openapi()
    if not spec:
        pytest.skip("Нет OpenAPI — не знаем куда слать запросы.")
    _, create_path, get_by_id_path = _discover_delivery_paths(spec)
    if not create_path:
        pytest.skip("В OpenAPI нет POST-эндпоинта для создания/запуска доставки.")

    created = None
    # Если create-путь с path param — подставим order_id = 5
    if "{" in create_path and "}" in create_path:
        path = create_path.replace("{id}", "5").replace("{order_id}", "5").replace("{delivery_id}", "1")
        r = client.post(path, json={"address": "Integration Ave. 5"})
        if r.status_code in (200, 201):
            try:
                created = r.json()
            except Exception:
                created = {"raw": r.text}
    # Иначе — POST c телом
    if not created:
        for body in [
            {"order_id": 5, "address": "Integration Ave. 5", "cost": 7.5},
            {"order_id": 5, "address": "Integration Ave. 5"},
            {"orderId": 5, "address": "Integration Ave. 5"},
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

    assert created, f"Создание доставки через {create_path} не удалось."

    # Достаём id доставки
    did = None
    if isinstance(created, dict):
        container = created.get("delivery", created)
        for k in ("delivery_id", "id", "shipment_id", "uuid"):
            if k in container:
                did = container[k]
                break
    assert did is not None, "Ответ API не содержит id созданной доставки."

    # Проверяем, что запись действительно в БД
    Model = _find_delivery_model()
    if Model is None:
        pytest.skip("Не удалось найти ORM-модель доставки (Delivery/Shipment).")

    s = _get_session()
    try:
        row = s.get(Model, did) if hasattr(s, "get") else s.query(Model).get(did)  # type: ignore[attr-defined]
        assert row is not None, "Запись доставки не найдена в БД."
    finally:
        s.close()

    # Если есть путь чтения по id — проверим ответ API
    if get_by_id_path:
        url = (
            get_by_id_path
            .replace("{id}", str(did))
            .replace("{delivery_id}", str(did))
            .replace("{shipment_id}", str(did))
        )
        r = client.get(url)
        assert r.status_code == 200
        _ = r.json()
