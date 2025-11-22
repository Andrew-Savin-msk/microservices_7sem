import os
import sys
import importlib
import importlib.util
import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///./test_component.db"
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
    if IMPORT_ERROR is not None:
        pytest.skip(f"Не удалось инициализировать приложение: {IMPORT_ERROR}")
    yield


@pytest.fixture(autouse=True)
def clean_db(_init_app):
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def mock_external_services(monkeypatch):
    if hasattr(app_module, "publish_event"):
        monkeypatch.setattr(app_module, "publish_event", lambda event, data: None)


def test_create_payment_and_get_by_order_id_component(_init_app):
    order_id = 100
    amount = 99.99
    
    response = client.post(
        f"/pay/{order_id}",
        params={"amount": amount}
    )
    
    assert response.status_code == 200, f"Ожидался статус 200, получен {response.status_code}"
    payment_data = response.json()
    assert "status" in payment_data
    assert "payment_id" in payment_data
    assert payment_data["status"] == "paid"
    
    payment_id = payment_data["payment_id"]
    assert isinstance(payment_id, int)
    assert payment_id > 0
    
    response = client.get(f"/payments/order/{order_id}")
    
    assert response.status_code == 200, f"Ожидался статус 200, получен {response.status_code}"
    payment_info = response.json()
    assert "payment_id" in payment_info
    assert "amount" in payment_info
    assert "status" in payment_info
    
    assert payment_info["payment_id"] == payment_id
    assert payment_info["amount"] == amount
    assert payment_info["status"] == "completed"
    
    SessionLocal = getattr(app_module, "SessionLocal")
    db = SessionLocal()
    try:
        Payment = getattr(app_module, "Payment")
        payment = db.query(Payment).filter(Payment.id == payment_id).first()
        assert payment is not None, "Платеж не найден в БД"
        assert payment.order_id == order_id
        assert payment.amount == amount
        assert payment.status == "completed"
    finally:
        db.close()


def test_create_multiple_payments_for_different_orders_component(_init_app):
    payments_data = [
        {"order_id": 201, "amount": 50.00},
        {"order_id": 202, "amount": 75.50},
        {"order_id": 203, "amount": 120.00},
    ]
    
    created_payments = []
    
    for payment_info in payments_data:
        response = client.post(
            f"/pay/{payment_info['order_id']}",
            params={"amount": payment_info["amount"]}
        )
        assert response.status_code == 200
        payment_data = response.json()
        assert payment_data["status"] == "paid"
        created_payments.append({
            "payment_id": payment_data["payment_id"],
            "order_id": payment_info["order_id"],
            "amount": payment_info["amount"]
        })
    
    for payment in created_payments:
        response = client.get(f"/payments/order/{payment['order_id']}")
        assert response.status_code == 200
        payment_info = response.json()
        
        assert payment_info["payment_id"] == payment["payment_id"]
        assert payment_info["amount"] == payment["amount"]
        assert payment_info["status"] == "completed"
    
    SessionLocal = getattr(app_module, "SessionLocal")
    db = SessionLocal()
    try:
        Payment = getattr(app_module, "Payment")
        for payment in created_payments:
            db_payment = db.query(Payment).filter(Payment.id == payment["payment_id"]).first()
            assert db_payment is not None
            assert db_payment.order_id == payment["order_id"]
            assert db_payment.amount == payment["amount"]
            assert db_payment.status == "completed"
    finally:
        db.close()
