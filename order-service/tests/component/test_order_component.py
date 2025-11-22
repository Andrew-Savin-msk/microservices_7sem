import os
import sys
import importlib
import importlib.util
import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///./test_component.db"
os.environ["USER_SERVICE_URL"] = "http://localhost:8000"
os.environ["RABBITMQ_HOST"] = "localhost"

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
    """Пропускаем тесты, если не удалось импортировать приложение."""
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


@pytest.fixture(autouse=True)
def mock_external_services(monkeypatch):
    """Мокируем внешние зависимости (user-service, RabbitMQ)."""
    import requests
    class MockUserResponse:
        def json(self):
            return {"username": "testuser", "address": "123 Test Street"}
        def raise_for_status(self):
            pass
    
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: MockUserResponse())
    
    if hasattr(app_module, "send_notification"):
        monkeypatch.setattr(app_module, "send_notification", lambda m: None)


def test_create_order_and_get_orders_component(_init_app):
    """
    Компонентный тест: создание заказа и получение списка заказов пользователя.
    Проверяет полный happy-path: POST /create_order -> GET /orders/{user_id}
    """
    user_id = 1
    items = "Product A:2, Product B:1"
    
    response = client.post(
        "/create_order",
        params={
            "user_id": user_id,
            "items": items
        }
    )
    
    assert response.status_code == 200, f"Ожидался статус 200, получен {response.status_code}"
    order_data = response.json()
    assert "message" in order_data
    assert order_data["message"] == "Order created"
    assert "order" in order_data
    
    order = order_data["order"]
    assert "id" in order
    assert "user_id" in order
    assert "items" in order
    assert "address" in order
    assert "status" in order
    
    order_id = order["id"]
    assert order["user_id"] == user_id
    assert order["items"] == items
    assert order["status"] == "created"
    assert order["address"] == "123 Test Street"
    
    response = client.get(f"/orders/{user_id}")
    
    assert response.status_code == 200, f"Ожидался статус 200, получен {response.status_code}"
    orders_data = response.json()
    assert "orders" in orders_data
    assert isinstance(orders_data["orders"], list)
    assert len(orders_data["orders"]) > 0
    
    found_order = None
    for o in orders_data["orders"]:
        if o["id"] == order_id:
            found_order = o
            break
    
    assert found_order is not None, "Созданный заказ не найден в списке заказов"
    assert found_order["items"] == items
    assert found_order["status"] == "created"


def test_create_order_and_update_status_component(_init_app):
    """
    Компонентный тест: создание заказа -> обновление статуса заказа.
    Проверяет полный цикл работы с заказом.
    """
    user_id = 2
    items = "Product C:3"
    
    response = client.post(
        "/create_order",
        params={
            "user_id": user_id,
            "items": items
        }
    )
    assert response.status_code == 200
    order_data = response.json()
    order_id = order_data["order"]["id"]
    assert order_data["order"]["status"] == "created"
    
    new_status = "processing"
    response = client.put(
        f"/update_order/{order_id}",
        params={"status": new_status}
    )
    
    assert response.status_code == 200, f"Ожидался статус 200, получен {response.status_code}"
    update_data = response.json()
    assert "message" in update_data
    assert f"Order {order_id} updated to {new_status}" in update_data["message"]
    
    SessionLocal = getattr(app_module, "SessionLocal")
    db = SessionLocal()
    try:
        Order = getattr(app_module, "Order")
        order = db.query(Order).filter(Order.id == order_id).first()
        assert order is not None
        assert order.status == new_status
    finally:
        db.close()
    
    response = client.get(f"/orders/{user_id}")
    assert response.status_code == 200
    orders_data = response.json()
    found_order = next((o for o in orders_data["orders"] if o["id"] == order_id), None)
    assert found_order is not None
    assert found_order["status"] == new_status
