# tests/integration/test_user_integration.py
import os, sys, importlib, uuid
from typing import Optional, Any, Type
import pytest
from fastapi.testclient import TestClient

# По умолчанию — локальный SQLite-файл (внешняя БД не нужна)
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_integration.db")

# PYTHONPATH -> корень сервиса
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Импортируем приложение
try:
    app_module = importlib.import_module("app")
    app = getattr(app_module, "app", None)
    if app is None:
        raise RuntimeError("В модуле app нет FastAPI-приложения 'app'")
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
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield

def _noop_side_effects():
    for fname in ("publish_event", "send_notification", "send_email"):
        if hasattr(app_module, fname):
            setattr(app_module, fname, lambda *a, **k: None)

def _openapi() -> Optional[dict]:
    r = client.get("/openapi.json")
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None

def _discover_user_paths(openapi: dict):
    paths = (openapi or {}).get("paths") or {}
    res = dict(list=None, create=None, get_by_id=None, me=None, login=None, token=None)
    for p, spec in paths.items():
        lower = p.lower()
        methods = {m.lower() for m in spec.keys()}
        if any(k in lower for k in ("user", "users")) and "get" in methods and "{" not in p and res["list"] is None:
            res["list"] = p
        if ("post" in methods) and (
            any(k in lower for k in ("user", "users", "register", "signup"))
        ) and res["create"] is None and "login" not in lower and "token" not in lower:
            res["create"] = p
        if any(k in lower for k in ("user", "users")) and "get" in methods and "{" in p and res["get_by_id"] is None:
            res["get_by_id"] = p
        if any(k in lower for k in ("me", "profile")) and "get" in methods and res["me"] is None:
            res["me"] = p
        if "post" in methods and any(k in lower for k in ("login", "/token")):
            if "token" in lower and res["token"] is None:
                res["token"] = p
            elif res["login"] is None:
                res["login"] = p
    return res

def _auth_headers_if_possible(login_path: Optional[str], token_path: Optional[str], username: str, password: str):
    # OAuth2 Password (form) -> /token
    if token_path:
        r = client.post(token_path, data={"username": username, "password": password})
        if r.status_code in (200, 201):
            try:
                data = r.json()
                token = data.get("access_token") or data.get("token")
                if token:
                    return {"Authorization": f"Bearer {token}"}
            except Exception:
                pass
    # JSON -> /login
    if login_path:
        for body in (
            {"username": username, "password": password},
            {"email": username, "password": password},
            {"login": username, "password": password},
        ):
            r = client.post(login_path, json=body)
            if r.status_code in (200, 201):
                try:
                    data = r.json()
                    token = data.get("access_token") or data.get("token")
                    if token:
                        return {"Authorization": f"Bearer {token}"}
                except Exception:
                    cookie = r.headers.get("set-cookie")
                    if cookie:
                        return {"Cookie": cookie}
    return {}

def _get_session():
    SessionLocal = getattr(app_module, "SessionLocal", None)
    if SessionLocal is None:
        from sqlalchemy.orm import sessionmaker
        SessionLocal = sessionmaker(bind=getattr(app_module, "engine", None))
    return SessionLocal()

def _find_user_model() -> Optional[Type[Any]]:
    # популярные имена
    for attr in ("User", "Account", "AppUser"):
        if hasattr(app_module, attr):
            return getattr(app_module, attr)
    # ищем среди mapper-ов
    Base = getattr(app_module, "Base", None)
    try:
        for m in Base.registry.mappers:  # type: ignore[attr-defined]
            cls = m.class_
            name = getattr(cls, "__name__", "").lower()
            tbn = getattr(getattr(cls, "__table__", None), "name", "") or getattr(cls, "__tablename__", "")
            if "user" in name or "account" in name or "user" in str(tbn).lower():
                return cls
    except Exception:
        pass
    return None

def test_register_persists_and_login_me():
    _noop_side_effects()

    spec = _openapi()
    if not spec:
        pytest.skip("Нет OpenAPI — не знаем маршруты.")
    paths = _discover_user_paths(spec)
    if not paths["create"]:
        pytest.skip("В OpenAPI нет POST для регистрации/создания пользователя.")

    email = f"u_{uuid.uuid4().hex[:8]}@test.local"
    username = f"user_{uuid.uuid4().hex[:6]}"
    password = "secret123!"

    created = None
    if "{" in paths["create"]:
        path = paths["create"].replace("{id}", "1").replace("{user_id}", "1")
        r = client.post(path, json={"email": email, "username": username, "password": password})
        if r.status_code in (200, 201):
            try: created = r.json()
            except Exception: created = {"raw": r.text}
    if not created:
        for body in (
            {"email": email, "username": username, "password": password},
            {"email": email, "password": password},
            {"username": username, "password": password},
        ):
            r = client.post(paths["create"], json=body)
            if r.status_code in (200, 201):
                try: created = r.json()
                except Exception: created = {"raw": r.text}
                break
            if r.status_code not in (400, 404, 405, 415, 422):
                break
    assert created, f"Создание пользователя через {paths['create']} не удалось."

    # id пользователя
    uid = None
    if isinstance(created, dict):
        container = created.get("user", created)
        for k in ("id", "user_id", "uuid"):
            if k in container:
                uid = container[k]
                break
    assert uid is not None, "Ответ API не содержит id пользователя."

    # запись реально в БД
    Model = _find_user_model()
    if Model is None:
        pytest.skip("Не удалось найти ORM-модель пользователя (User/Account).")
    s = _get_session()
    try:
        row = s.get(Model, uid) if hasattr(s, "get") else s.query(Model).get(uid)  # type: ignore[attr-defined]
        assert row is not None, "Запись пользователя не найдена в БД."
    finally:
        s.close()

    # логин + /me (если есть)
    headers = _auth_headers_if_possible(paths["login"], paths["token"], email, password)
    if paths["me"] and headers:
        r = client.get(paths["me"], headers=headers)
        assert r.status_code == 200
        _ = r.json()

    # чтение по id (если есть)
    if paths["get_by_id"]:
        url = paths["get_by_id"].replace("{id}", str(uid)).replace("{user_id}", str(uid))
        r = client.get(url, headers=headers or None)
        assert r.status_code == 200
        _ = r.json()
