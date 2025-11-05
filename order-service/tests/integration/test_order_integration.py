import os
import sys, os

import importlib
import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="DATABASE_URL not provided (run in CI or export env before run)"
)

app_module = importlib.import_module("app")
client = TestClient(app_module.app)

@pytest.fixture(autouse=True)
def clean_db():
    app_module.Base.metadata.drop_all(bind=app_module.engine)
    app_module.Base.metadata.create_all(bind=app_module.engine)
    yield

def test_create_persists_in_db(monkeypatch):
    import requests
    class OK:
        def json(self): return {"address": "Real DB Addr"}
        def raise_for_status(self): pass
    monkeypatch.setattr(requests, "get", lambda url: OK())
    monkeypatch.setattr(app_module, "send_notification", lambda m: None)

    r = client.post("/create_order", params={"user_id": 5, "items": "ABC:2"})
    assert r.status_code == 200
    oid = r.json()["order"]["id"]

    s = app_module.SessionLocal()
    try:
        row = s.query(app_module.Order).get(oid)
        assert row is not None
        assert row.user_id == 5
        assert row.status == "created"
    finally:
        s.close()
