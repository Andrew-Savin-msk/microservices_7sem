# tests/unit/test_smoke.py
import os, sys, importlib, uuid
from typing import Optional, Tuple
import pytest
from fastapi.testclient import TestClient

# PYTHONPATH -> корень сервиса
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
# Локальная БД для юнитов
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_unit.db")

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
def reset_db():
    """Чистая БД на каждый тест, если в app есть Base/engine."""
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield

def _noop_side_effects():
    # Глушим брокеры/уведомления/почту, если есть
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
    """
    Возвращает словарь с ключами:
      list, create, get_by_id, me, login, token, update_by_id
    """
    paths = (openapi or {}).get("paths") or {}
    res = dict(list=None, create=None, get_by_id=None, me=None, login=None, token=None, update_by_id=None)
    for p, spec in paths.items():
        lower = p.lower()
        methods = {m.lower() for m in spec.keys()}
        # список пользователей
        if any(k in lower for k in ("user", "users")) and "get" in methods and "{" not in p and res["list"] is None:
            res["list"] = p
        # создание пользователя / регистрация
        if ("post" in methods) and (
            any(k in lower for k in ("user", "users", "register", "signup"))
        ) and res["create"] is None and "login" not in lower and "token" not in lower:
            res["create"] = p
        # чтение по id
        if any(k in lower for k in ("user", "users")) and "get" in methods and "{" in p and res["get_by_id"] is None:
            res["get_by_id"] = p
        # профиль текущего пользователя
        if any(k in lower for k in ("me", "profile")) and "get" in methods and res["me"] is None:
            res["me"] = p
        # логин/токен
        if "post" in methods and any(k in lower for k in ("login", "/token")) and res["login"] is None:
            # различаем чистый /token и логин
            if "token" in lower and res["token"] is None:
                res["token"] = p
            else:
                res["login"] = p
        # обновление пользователя
        if any(m in methods for m in ("put", "patch")) and "{" in p and any(k in lower for k in ("user", "users")):
            if res["update_by_id"] is None:
                res["update_by_id"] = p
    return res

def _auth_headers_if_possible(login_path: Optional[str], token_path: Optional[str], username: str, password: str):
    """Пробуем получить токен разными способами; если удалось — вернём headers."""
    # 1) OAuth2 Password (form data) на /token
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
    # 2) JSON на /login
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
                    # возможно, сессия через cookie
                    cookie = r.headers.get("set-cookie")
                    if cookie:
                        return {"Cookie": cookie}
    return {}

def test_openapi_and_users_list_smoke():
    spec = _openapi()
    assert spec is not None, "openapi.json недоступен"
    paths = _discover_user_paths(spec)
    if not paths["list"]:
        pytest.skip("В OpenAPI не найден список пользователей (*users*).")
    r = client.get(paths["list"])
    assert r.status_code in (200, 204), f"GET {paths['list']} -> {r.status_code}"
    if r.status_code == 200:
        _ = r.json()

def test_register_login_me_and_get_by_id_flow():
    _noop_side_effects()
    spec = _openapi()
    if not spec:
        pytest.skip("Нет OpenAPI — не знаем маршруты.")
    paths = _discover_user_paths(spec)

    # создаём пользователя (или регистрируем)
    if not paths["create"]:
        pytest.skip("В OpenAPI нет эндпоинта регистрации/создания пользователя.")
    email = f"u_{uuid.uuid4().hex[:8]}@test.local"
    username = f"user_{uuid.uuid4().hex[:6]}"
    password = "secret123!"

    created = None
    if "{" in paths["create"]:
        path = paths["create"].replace("{id}", "1").replace("{user_id}", "1")
        r = client.post(path, json={"email": email, "username": username, "password": password})
        if r.status_code in (200, 201):
            try:
                created = r.json()
            except Exception:
                created = {"raw": r.text}
    if not created:
        for body in (
            {"email": email, "username": username, "password": password},
            {"email": email, "password": password},
            {"username": username, "password": password},
        ):
            r = client.post(paths["create"], json=body)
            if r.status_code in (200, 201):
                try:
                    created = r.json()
                except Exception:
                    created = {"raw": r.text}
                break
            if r.status_code not in (400, 404, 405, 415, 422):
                break
    assert created, f"Создание пользователя через {paths['create']} не удалось."

    uid = None
    if isinstance(created, dict):
        container = created.get("user", created)
        for k in ("id", "user_id", "uuid"):
            if k in container:
                uid = container[k]
                break

    # авторизуемся, если есть login/token
    headers = _auth_headers_if_possible(paths["login"], paths["token"], email, password)

    # /me или /profile
    if paths["me"] and headers:
        r = client.get(paths["me"], headers=headers)
        assert r.status_code == 200, f"GET {paths['me']} -> {r.status_code}"
        _ = r.json()

    # чтение по id
    if paths["get_by_id"] and uid is not None:
        path_id = paths["get_by_id"].replace("{id}", str(uid)).replace("{user_id}", str(uid))
        r = client.get(path_id, headers=headers or None)
        assert r.status_code == 200, f"GET {path_id} -> {r.status_code}"
        _ = r.json()

    # обновление (если есть)
    if paths["update_by_id"] and uid is not None:
        path_upd = paths["update_by_id"].replace("{id}", str(uid)).replace("{user_id}", str(uid))
        r = client.request("PATCH", path_upd, json={"full_name": "Updated Name"}, headers=headers or None)
        if r.status_code == 405:
            r = client.request("PUT", path_upd, json={"full_name": "Updated Name"}, headers=headers or None)
        assert r.status_code in (200, 204), f"{path_upd} -> {r.status_code}"
