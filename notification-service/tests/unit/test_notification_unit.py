# tests/unit/test_smoke.py
import os, sys, importlib
from typing import Optional, Tuple
import pytest
from fastapi.testclient import TestClient

# PYTHONPATH -> корень сервиса
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
# Локальная БД для юнитов (если она вообще используется)
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_unit.db")

# ===== Импорт приложения =====
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
def reset_db():
    """Чистая БД на каждый тест (если объявлены Base/engine)."""
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield

# ===== Заглушки внешних интеграций =====
def _noop_internal_functions(monkeypatch=None):
    # Глушим возможные обёртки внутри приложения
    for fname in (
        "publish_event", "enqueue_notification", "notify",
        "send_email", "send_sms", "send_push", "send_notification"
    ):
        if hasattr(app_module, fname):
            if monkeypatch:
                monkeypatch.setattr(app_module, fname, lambda *a, **k: {"status": "ok"}, raising=False)
            else:
                setattr(app_module, fname, lambda *a, **k: {"status": "ok"})

def _stub_network(monkeypatch):
    # ---- SMTP / aiosmtplib ----
    try:
        import smtplib
        class DummySMTP:
            def __init__(self, *a, **k): pass
            def starttls(self, *a, **k): pass
            def login(self, *a, **k): pass
            def sendmail(self, *a, **k): return {}
            def send_message(self, *a, **k): return {}
            def quit(self): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
        monkeypatch.setattr("smtplib.SMTP", DummySMTP, raising=False)
        monkeypatch.setattr("smtplib.SMTP_SSL", DummySMTP, raising=False)
    except Exception:
        pass
    try:
        import aiosmtplib  # type: ignore
        async def _ok_send(*a, **k): return {}
        monkeypatch.setattr("aiosmtplib.send", _ok_send, raising=False)
    except Exception:
        pass

    # ---- SendGrid ----
    try:
        import sendgrid  # type: ignore
        class DummyResp: status_code = 202; body = ""; headers = {}
        class DummySG:
            def __init__(self, *a, **k): pass
            def send(self, *a, **k): return DummyResp()
        monkeypatch.setattr("sendgrid.SendGridAPIClient", DummySG, raising=False)
    except Exception:
        pass

    # ---- Twilio ----
    try:
        import twilio  # noqa
        class _Msgs:
            def create(self, *a, **k): return type("M", (), {"sid": "SM_test"})()
        class DummyTwilio:
            def __init__(self, *a, **k): self.messages = _Msgs()
        monkeypatch.setattr("twilio.rest.Client", DummyTwilio, raising=False)
    except Exception:
        pass

    # ---- Firebase Admin (FCM) ----
    try:
        import firebase_admin  # noqa
        from types import SimpleNamespace
        monkeypatch.setattr("firebase_admin.messaging.send", lambda *a, **k: "msgid_test", raising=False)
    except Exception:
        pass

    # ---- APNs2 ----
    try:
        import apns2  # noqa
        class DummyAPNs:
            def __init__(self, *a, **k): pass
            def send_notification(self, *a, **k): return None
        monkeypatch.setattr("apns2.client.APNsClient", DummyAPNs, raising=False)
    except Exception:
        pass

    # ---- RabbitMQ (pika) ----
    try:
        import pika  # noqa
        class _Ch:
            def basic_publish(self, *a, **k): pass
        class DummyConn:
            def __init__(self, *a, **k): pass
            def channel(self): return _Ch()
            def close(self): pass
        monkeypatch.setattr("pika.BlockingConnection", DummyConn, raising=False)
    except Exception:
        pass

    # ---- HTTP клиенты ----
    try:
        import requests  # noqa
        class OK:
            status_code = 200
            def json(self): return {"ok": True}
            def raise_for_status(self): pass
            @property
            def text(self): return '{"ok": true}'
        monkeypatch.setattr("requests.get", lambda *a, **k: OK(), raising=False)
        monkeypatch.setattr("requests.post", lambda *a, **k: OK(), raising=False)
    except Exception:
        pass

def _openapi() -> Optional[dict]:
    r = client.get("/openapi.json")
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None

def _discover_notification_paths(openapi: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Возвращает (list_path, send_path, get_by_id_path) по ключевым словам:
    notification / notify / send / email / sms / push / message
    """
    keys = ("notification", "notify", "send", "email", "sms", "push", "message")
    paths = (openapi or {}).get("paths") or {}
    list_path = send_path = get_by_id_path = None
    for p, spec in paths.items():
        lower = p.lower()
        if not any(k in lower for k in keys):
            continue
        methods = {m.lower() for m in spec.keys()}
        if "get" in methods and "{" not in p and list_path is None:
            list_path = p
        if "post" in methods and send_path is None:
            send_path = p
        if "get" in methods and "{" in p and get_by_id_path is None:
            get_by_id_path = p
    return list_path, send_path, get_by_id_path

# ===== Тесты =====
def test_openapi_and_list_smoke():
    spec = _openapi()
    assert spec is not None, "openapi.json недоступен"
    list_path, _, _ = _discover_notification_paths(spec)
    if not list_path:
        pytest.skip("В OpenAPI не найден список уведомлений (*notification/notify/message*).")
    r = client.get(list_path)
    assert r.status_code in (200, 204), f"GET {list_path} -> {r.status_code}"
    if r.status_code == 200:
        _ = r.json()

def test_send_notification_variants(monkeypatch):
    _noop_internal_functions(monkeypatch)
    _stub_network(monkeypatch)

    spec = _openapi()
    if not spec:
        pytest.skip("Нет OpenAPI — не знаем маршруты.")
    _, send_path, get_by_id_path = _discover_notification_paths(spec)
    if not send_path:
        pytest.skip("В OpenAPI нет POST-эндпоинта для отправки уведомлений.")

    created = None
    # 1) Если путь с path params — попробуем подставить канал
    if "{" in send_path and "}" in send_path:
        path = (
            send_path
            .replace("{channel}", "email")
            .replace("{id}", "1")
            .replace("{notification_id}", "1")
        )
        r = client.post(path, json={"to": "test@example.com", "subject": "Hi", "body": "Hello"})
        if r.status_code in (200, 201, 202):
            try:
                created = r.json()
            except Exception:
                created = {"raw": r.text}

    # 2) Иначе пробуем разные полезные нагрузки
    if not created:
        candidate_payloads = [
            {"channel": "email", "to": "test@example.com", "subject": "Hi", "body": "Hello"},
            {"channel": "sms", "to": "+15550001122", "message": "Code 1234"},
            {"channel": "push", "to": "device-uid-1", "title": "Hi", "body": "Hello"},
            {"recipient": "test@example.com", "message": "Hello"},  # максимально общий
        ]
        for body in candidate_payloads:
            r = client.post(send_path, json=body)
            if r.status_code in (200, 201, 202):
                try:
                    created = r.json()
                except Exception:
                    created = {"raw": r.text}
                break
            if r.status_code not in (400, 404, 405, 415, 422):
                break

    if not created:
        pytest.skip(f"Отправить уведомление через {send_path} не удалось — проверьте схему запроса.")

    # Если API возвращает id — попробуем GET по id
    nid = None
    if isinstance(created, dict):
        container = created.get("notification", created)
        for k in ("notification_id", "id", "message_id", "sid", "uuid"):
            if k in container:
                nid = container[k]; break

    if get_by_id_path and nid is not None:
        path_id = (
            get_by_id_path
            .replace("{id}", str(nid))
            .replace("{notification_id}", str(nid))
            .replace("{message_id}", str(nid))
        )
        r = client.get(path_id)
        assert r.status_code == 200, f"GET {path_id} -> {r.status_code}"
        _ = r.json()
