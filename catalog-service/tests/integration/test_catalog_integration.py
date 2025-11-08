import os, sys, importlib
import pytest
from fastapi.testclient import TestClient

# по умолчанию локальный файл SQLite, чтобы не требовать внешний сервер БД
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_integration.db")

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
app_module = importlib.import_module("app")
client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def clean_db():
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield

def _noop_publish_event_if_exists():
    if hasattr(app_module, "publish_event"):
        setattr(app_module, "publish_event", lambda *a, **k: None)

def _openapi() -> Optional[dict]:
    r = client.get("/openapi.json")
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None

def _discover_paths(openapi: dict):
    paths = (openapi or {}).get("paths") or {}
    list_path = create_path = get_by_id_path = None
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
    return list_path, create_path, get_by_id_path

def _get_session():
    SessionLocal = getattr(app_module, "SessionLocal", None)
    if SessionLocal is None:
        from sqlalchemy.orm import sessionmaker
        SessionLocal = sessionmaker(bind=app_module.engine)
    return SessionLocal()

def _find_product_model() -> Optional[Type[Any]]:
    # прямое имя
    for attr in ("Product", "CatalogProduct", "Item", "CatalogItem"):
        if hasattr(app_module, attr):
            return getattr(app_module, attr)
    # поиск по mapper-ам SQLAlchemy
    Base = getattr(app_module, "Base", None)
    try:
        for m in Base.registry.mappers:
            cls = m.class_
            name = getattr(cls, "__name__", "").lower()
            tbn = getattr(getattr(cls, "__table__", None), "name", "") or getattr(cls, "__tablename__", "")
            if any(s in name for s in ("product", "catalogitem", "item")) or any(s in str(tbn).lower() for s in ("product", "item")):
                return cls
    except Exception:
        pass
    return None

def test_create_persists_in_db(monkeypatch):
    _noop_publish_event_if_exists()

    spec = _openapi()
    if not spec:
        pytest.skip("Нет OpenAPI — не знаем куда слать запросы.")
    list_path, create_path, get_by_id_path = _discover_paths(spec)
    if not create_path:
        pytest.skip("В OpenAPI нет POST для создания продукта.")

    payloads = [
        {"name": "Real Product", "price": 19.99, "stock": 5},
        {"title": "Real Product", "price": 19.99, "quantity": 5},
        {"name": "Real Product", "price": 19.99},
    ]
    created = None
    for body in payloads:
        r = client.post(create_path, json=body)
        if r.status_code in (200, 201):
            created = r.json()
            break
        if r.status_code not in (400, 404, 405, 415, 422):
            break
    assert created, f"Создание продукта через {create_path} не удалось."

    pid = None
    if isinstance(created, dict):
        # допускаем оболочку {"product": {...}}
        container = created.get("product", created)
        for k in ("id", "product_id", "uuid"):
            if k in container:
                pid = container[k]
                break
    assert pid, "Ответ API не содержит id созданного продукта."

    # проверяем, что запись действительно в БД
    Model = _find_product_model()
    if Model is None:
        pytest.skip("Не удалось найти ORM-модель продукта в app.py (Product/Item).")

    s = _get_session()
    try:
        row = None
        # SQLAlchemy 1.4+: Session.get, 1.3: Query.get
        if hasattr(s, "get"):
            row = s.get(Model, pid)
        else:
            row = s.query(Model).get(pid)  # type: ignore[attr-defined]
        assert row is not None, "Запись продукта не найдена в БД."
    finally:
        s.close()

    # если задекларирован путь чтения по id — проверим корректность ответа
    if get_by_id_path:
        url = get_by_id_path.replace("{id}", str(pid)).replace("{product_id}", str(pid))
        r = client.get(url)
        assert r.status_code == 200
        _ = r.json()
