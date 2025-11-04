import os
import importlib
import types
import pytest
from fastapi.testclient import TestClient

# в tests/*/test_*.py
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


# Используем файловую SQLite, чтобы не плодить внешние зависимости в unit
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_unit.db")

# Импортируем модуль приложения
app_module = importlib.import_module("app")  # в твоём репо файл называется app.py
client = TestClient(app_module.app)

@pytest.fixture(autouse=True)
def reset_db():
    # Полностью пересоздаём таблицы перед каждым тестом
    app_module.Base.metadata.drop_all(bind=app_module.engine)
    app_module.Base.metadata.create_all(bind=app_module.engine)
    yield

def test_create_order_success(monkeypatch):
    # Мокаем HTTP в user-service
    import requests
    class OK:
        status_code = 200
        def json(self): return {"address": "Mock Ave 1"}
        def raise_for_status(self): pass
    monkeypatch.setattr(requests, "get", lambda url: OK())

    # Мокаем отправку в RabbitMQ
    called = {"msg": None}
    monkeypatch.setattr(app_module, "send_notification", lambda m: called.update(msg=m))

    r = client.post("/create_order", params={"user_id": 7, "items": "sku1:2,sku2:1"})
    assert r.status_code == 200
    data = r.json()["order"]
    assert data["user_id"] == 7
    assert data["address"] == "Mock Ave 1"
    assert data["status"] == "created"
    assert "Order created for user 7" in called["msg"]

def test_create_order_user_not_found(monkeypatch):
    import requests
    class Err:
        def raise_for_status(self): raise requests.HTTPError("404")
    monkeypatch.setattr(requests, "get", lambda url: Err())

    r = client.post("/create_order", params={"user_id": 999, "items": "x"})
    assert r.status_code == 404
    assert r.json()["detail"] == "User not found or service unavailable"

def test_get_and_update_flow(monkeypatch):
    # user-service ок
    import requests
    class OK:
        def json(self): return {"address": "UL. Test, 1"}
        def raise_for_status(self): pass
    monkeypatch.setattr(requests, "get", lambda url: OK())
    monkeypatch.setattr(app_module, "send_notification", lambda m: None)

    # Создаём заказ
    r = client.post("/create_order", params={"user_id": 1, "items": "A:1"})
    order_id = r.json()["order"]["id"]

    # Список заказов пользователя
    r2 = client.get("/orders/1")
    assert r2.status_code == 200
    assert any(o["id"] == order_id for o in r2.json()["orders"])

    # Обновляем статус
    r3 = client.put(f"/update_order/{order_id}", params={"status": "shipped"})
    assert r3.status_code == 200
    assert "updated to shipped" in r3.json()["message"]
