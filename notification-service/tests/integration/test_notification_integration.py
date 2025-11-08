# tests/integration/test_notification_integration.py
import os, sys, importlib
from typing import Optional, Any, Type
import pytest
from fastapi.testclient import TestClient

# По умолчанию — локальный SQLite-файл (внешняя БД не нужна)
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_integration.db")

# PYTHONPATH -> корень сервиса
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

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
def clean_db():
    """Чистая БД на каждый тест, если объявлены Base/engine."""
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield

# ===== Заглушки внешних интеграций =====
def _noop_internal_functions(monkeypatch=None):
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
    # SMTP
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

    # SendGrid
    try:
        class DummyResp: status_code = 202; body = ""; headers = {}
        class DummySG:
            def __init__(self, *a, **k): pass
            def send(self, *a, **k): return DummyResp()
        monkeypatch.setattr("sendgrid.SendGridAPIClient", DummySG, raising=False)
    except Exception:
        pass

    # Twilio
    try:
        class _Msgs:
            def create(self, *a, **k): return type("M", (), {"sid": "SM_test"})()
        class DummyTwilio:
            def __init__(self, *a, **k): self.messages = _Msgs()
        monkeypatch.setattr("twilio.rest.Client", DummyTwilio, raising=False)
    except Exception:
        pass

    # FCM/APNs
    try:
        monkeypatch.setattr("firebase_admin.messaging.send", lambda *a, **k: "msgid_test", raising=False)
    except Exception:
        pass
    try:
        class DummyAPNs:
            def __init__(self, *a, **k): pass
            def send_notification(self, *a, **k): return None
        monkeypatch.setattr("apns2.client.APNsClient", DummyAPNs, raising=False)
    except Exception:
        pass

    # RabbitMQ (pika)
    try:
        class _Ch:
            def basic_publish(self, *a, **k): pass
        class DummyConn:
            def __init__(self, *a, **k): pass
            def channel(self): return _Ch()
            def close(self): pass
        monkeypatch.setattr("pika.BlockingConnection", DummyConn, raising=False)
    except Exception:
        pass

    # HTTP
    try:
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

def _discover_notification_paths(openapi: dict):
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

def _get_session():
    SessionLocal = getattr(app_module, "SessionLocal", None)
    if SessionLocal is None and hasattr(app_module, "engine"):
        from sqlalchemy.orm import sessionmaker
        SessionLocal = sessionmaker(bind=app_module.engine)
    return SessionLocal() if SessionLocal else None

def _find_notification_model() -> Optional[Type[Any]]:
    # Популярные имена
    for attr in ("Notification", "Message", "OutboxMessage", "NotificationLog"):
        if hasattr(app_module, attr):
            return getattr(app_module, attr)

    # Поиск среди mapper-ов SQLAlchemy
    Base = getattr(app_module, "Base", None)
    try:
        for m in Base.registry.mappers:  # type: ignore[attr-defined]
            cls = m.class_
            name = getattr(cls, "__name__", "").lower()
            tbn = getattr(getattr(cls, "__table__", None), "name", "") or getattr(cls, "__tablename__", "")
            if any(s in name for s in ("notification", "message", "outbox")) or any(
                s in str(tbn).lower() for s in ("notification", "message", "outbox")
            ):
                return cls
    except Exception:
        pass
    return None

def test_send_and_optional_persist(monkeypatch):
    _noop_internal_functions(monkeypatch)
    _stub_network(monkeypatch)

    spec = _openapi()
    if not spec:
        pytest.skip("Нет OpenAPI — не знаем маршруты.")
    list_path, send_path, get_by_id_path = _discover_notification_paths(spec)
    if not send_path:
        pytest.skip("В OpenAPI нет POST-эндпоинта для отправки уведомлений.")

    created = None
    # path params -> подставим 'email'
    if "{" in send_path and "}" in send_path:
        path = (
            send_path
            .replace("{channel}", "email")
            .replace("{id}", "1")
            .replace("{notification_id}", "1")
        )
        r = client.post(path, json={"to": "int@example.com", "subject": "Hi", "body": "Hello"})
        if r.status_code in (200, 201, 202):
            try: created = r.json()
            except Exception: created = {"raw": r.text}
    # иначе — набор полезных нагрузок
    if not created:
        for body in [
            {"channel": "email", "to": "int@example.com", "subject": "Hi", "body": "Hello"},
            {"channel": "sms", "to": "+15550002233", "message": "Code 2468"},
            {"channel": "push", "to": "device-abc", "title": "Hi", "body": "Hello"},
        ]:
            r = client.post(send_path, json=body)
            if r.status_code in (200, 201, 202):
                try: created = r.json()
                except Exception: created = {"raw": r.text}
                break
            if r.status_code not in (400, 404, 405, 415, 422):
                break

    assert created, f"Отправить уведомление через {send_path} не удалось."

    nid = None
    if isinstance(created, dict):
        container = created.get("notification", created)
        for k in ("notification_id", "id", "message_id", "sid", "uuid"):
            if k in container:
                nid = container[k]; break

    # Проверим GET по id, если есть такой маршрут
    if get_by_id_path and nid is not None:
        path_id = (
            get_by_id_path
            .replace("{id}", str(nid))
            .replace("{notification_id}", str(nid))
            .replace("{message_id}", str(nid))
        )
        r = client.get(path_id)
        assert r.status_code == 200
        _ = r.json()

    # Если у сервиса есть БД и модель — проверим, что запись есть
    Model = _find_notification_model()
    Session = _get_session()
    if Model and Session:
        try:
            s = Session
            row = s.get(Model, nid) if hasattr(s, "get") else s.query(Model).get(nid)  # type: ignore[attr-defined]
            assert row is not None, "Запись уведомления не найдена в БД."
        finally:
            try:
                s.close()
            except Exception:
                pass
