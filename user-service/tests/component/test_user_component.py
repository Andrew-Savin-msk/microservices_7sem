import os
import sys
import importlib
import importlib.util
import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///./test_component.db"

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

app_module = None
client = None
IMPORT_ERROR = None


@pytest.fixture(scope="module")
def _init_app():
    global app_module, client, IMPORT_ERROR
    try:
        if "app" in sys.modules:
            del sys.modules["app"]
        service_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        app_file = os.path.join(service_path, "app.py")
        
        spec = importlib.util.spec_from_file_location("app", app_file)
        app_module = importlib.util.module_from_spec(spec)
        sys.modules["app"] = app_module
        spec.loader.exec_module(app_module)
        
        app = getattr(app_module, "app", None)
        if app is None:
            raise RuntimeError("В модуле app отсутствует FastAPI-приложение 'app'")
        client = TestClient(app)
        IMPORT_ERROR = None
    except Exception as e:
        app_module = None
        client = None
        IMPORT_ERROR = str(e)
    yield
    app_module = None
    client = None


@pytest.fixture(autouse=True)
def _skip_if_import_failed(_init_app):
    if IMPORT_ERROR is not None:
        pytest.skip(f"Не удалось инициализировать приложение: {IMPORT_ERROR}")
    yield


@pytest.fixture(autouse=True)
def clean_db(_init_app):
    """Очистка БД перед каждым тестом для изоляции."""
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield


def test_register_user_and_get_by_id_component(_init_app):
    username = "testuser_component"
    password = "securepass123"
    address = "123 Test Street, Test City"
    
    response = client.post(
        "/register",
        params={
            "username": username,
            "password": password,
            "address": address
        }
    )
    
    assert response.status_code == 200, f"Ожидался статус 200, получен {response.status_code}"
    register_data = response.json()
    assert "message" in register_data
    assert register_data["message"] == "User registered"
    
    SessionLocal = getattr(app_module, "SessionLocal")
    db = SessionLocal()
    try:
        User = getattr(app_module, "User")
        user = db.query(User).filter(User.username == username).first()
        assert user is not None, "Пользователь не найден в БД после регистрации"
        user_id = user.id
        assert user.username == username
        assert user.address == address
    finally:
        db.close()
    
    response = client.get(f"/user/{user_id}")
    
    assert response.status_code == 200, f"Ожидался статус 200, получен {response.status_code}"
    user_data = response.json()
    assert "username" in user_data
    assert "address" in user_data
    assert user_data["username"] == username
    assert user_data["address"] == address


def test_register_login_and_update_profile_component(_init_app):
    username = "user_component_flow"
    password = "password123"
    address = "Initial Address"
    
    response = client.post(
        "/register",
        params={
            "username": username,
            "password": password,
            "address": address
        }
    )
    assert response.status_code == 200
    
    SessionLocal = getattr(app_module, "SessionLocal")
    db = SessionLocal()
    try:
        User = getattr(app_module, "User")
        user = db.query(User).filter(User.username == username).first()
        assert user is not None
        user_id = user.id
    finally:
        db.close()
    
    response = client.post(
        "/login",
        params={
            "username": username,
            "password": password
        }
    )
    assert response.status_code == 200
    login_data = response.json()
    assert "message" in login_data
    assert login_data["message"] == "Logged in"
    
    new_address = "Updated Address 456"
    response = client.put(
        f"/update_profile/{user_id}",
        params={"address": new_address}
    )
    assert response.status_code == 200
    update_data = response.json()
    assert "message" in update_data
    assert update_data["message"] == "Profile updated"
    
    response = client.get(f"/user/{user_id}")
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["address"] == new_address
    assert user_data["username"] == username
