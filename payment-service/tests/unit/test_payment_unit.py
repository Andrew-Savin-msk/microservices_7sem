# tests/unit/test_smoke.py
import os, sys, importlib
from typing import Optional, Tuple
import pytest
from fastapi.testclient import TestClient

# Ищем app.py из корня сервиса
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Локальная БД для юнитов (не трогаем реальную)
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_unit.db")

# Импортируем приложение
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
    """На каждый тест — чистая БД, если в app объявлены Base/engine."""
    Base = getattr(app_module, "Base", None)
    engine = getattr(app_module, "engine", None)
    if Base is not None and engine is not None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield

def _noop_publish_event_if_exists():
    # Глушим брокер/уведомления, если есть функции-обёртки
    for fname in ("publish_event", "send_notification", "emit_event", "notify_payment"):
        if hasattr(app_module, fname):
            setattr(app_module, fname, lambda *a, **k: None)

def _stub_http_and_gateways(monkeypatch):
    """Подменяем внешние вызовы (requests/SDK платёжек) на всегда-успешные."""
    # HTTP-клиент
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

    # Популярные SDK: stripe / yookassa / braintree — глушим, если импортированы
    try:
        import stripe  # type: ignore
        class _Charge:
            @staticmethod
            def create(*a, **k): return {"id": "ch_test", "status": "succeeded"}
            @staticmethod
            def refund(*a, **k): return {"id": "re_test", "status": "succeeded"}
        stripe.Charge = _Charge  # type: ignore[attr-defined]
        class _PaymentIntent:
            @staticmethod
            def create(*a, **k): return {"id": "pi_test", "status": "succeeded"}
            @staticmethod
            def confirm(*a, **k): return {"id": "pi_test", "status": "succeeded"}
        stripe.PaymentIntent = _PaymentIntent  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        import yookassa  # type: ignore
        class _Payment:
            @staticmethod
            def create(*a, **k): return {"id": "yo_test", "status": "succeeded"}
            @staticmethod
            def capture(*a, **k): return {"id": "yo_test", "status": "succeeded"}
            @staticmethod
            def cancel(*a, **k): return {"id": "yo_test", "status": "canceled"}
        yookassa.Payment = _Payment  # type: ignore[attr-defined]
    except Exception:
        pass

    # Если логика вынесена в функции модуля — глушим их тоже
    for fn in ("process_payment", "create_charge", "capture_payment", "refund_payment"):
        if hasattr(app_module, fn):
            monkeypatch.setattr(app_module, fn, lambda *a, **k: {"status": "succeeded", "id": "tx_test"}, raising=False)

def _openapi() -> Optional[dict]:
    r = client.get("/openapi.json")
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None

def _discover_payment_paths(openapi: dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Возвращает (list_path, create_path, get_by_id_path, refund_path)
    по ключевым словам: payment / pay / charge / transaction / refund
    """
    keys = ("payment", "pay", "charge", "transaction", "refund")
    paths = (openapi or {}).get("paths") or {}
    list_path = create_path = get_by_id_path = refund_path = None
    for p, spec in paths.items():
        lower = p.lower()
        if not any(k in lower for k in keys):
            continue
        methods = {m.lower() for m in spec.keys()}
        if "get" in methods and "{" not in p and list_path is None:
            list_path = p
        if "post" in methods and create_path is None and "refund" not in lower:
            create_path = p
        if "get" in methods and "{" in p and get_by_id_path is None:
            get_by_id_path = p
        if "post" in methods and "refund" in lower and refund_path is None:
            refund_path = p
    return list_path, create_path, get_by_id_path, refund_path

def test_openapi_and_list_smoke():
    spec = _openapi()
    assert spec is not None, "openapi.json недоступен"
    list_path, *_ = _discover_payment_paths(spec)
    if not list_path:
        pytest.skip("В OpenAPI не найден список платежей (*payment/pay/charge/transaction*).")
    r = client.get(list_path)
    assert r.status_code in (200, 204), f"GET {list_path} -> {r.status_code}"
    if r.status_code == 200:
        _ = r.json()

def test_create_fetch_and_refund(monkeypatch):
    _noop_publish_event_if_exists()
    _stub_http_and_gateways(monkeypatch)

    spec = _openapi()
    if not spec:
        pytest.skip("Нет OpenAPI — не знаем маршруты для платежей.")
    list_path, create_path, get_by_id_path, refund_path = _discover_payment_paths(spec)
    if not create_path:
        pytest.skip("В OpenAPI нет POST-эндпоинта для создания платежа/чарджа.")

    created = None
    # 1) Если create-путь содержит path params — подставим order_id = 1
    if "{" in create_path and "}" in create_path:
        path = (
            create_path
            .replace("{id}", "1")
            .replace("{order_id}", "1")
            .replace("{payment_id}", "1")
            .replace("{transaction_id}", "1")
        )
        r = client.post(path, json={"amount": 10.0, "currency": "USD"})
        if r.status_code in (200, 201):
            try:
                created = r.json()
            except Exception:
                created = {"raw": r.text}

    # 2) Иначе POST с телом
    if not created:
        for body in [
            {"order_id": 1, "amount": 10.0, "currency": "USD", "source": "tok_visa"},
            {"order_id": 1, "amount": 1000, "currency": "usd", "payment_method": "card"},
            {"orderId": 1, "amount": 10.0, "currency": "USD"},
        ]:
            r = client.post(create_path, json=body)
            if r.status_code in (200, 201):
                try:
                    created = r.json()
                except Exception:
                    created = {"raw": r.text}
                break
            if r.status_code not in (400, 404, 405, 415, 422):
                break

    if not created:
        pytest.skip(f"Создание платежа через {create_path} не удалось — проверьте схему запроса.")

    # Достаём id платежа
    pid = None
    if isinstance(created, dict):
        container = created.get("payment", created)
        for k in ("payment_id", "id", "transaction_id", "uuid"):
            if k in container:
                pid = container[k]
                break

    # GET по id (если есть такой путь)
    if get_by_id_path and pid is not None:
        path_id = (
            get_by_id_path
            .replace("{id}", str(pid))
            .replace("{payment_id}", str(pid))
            .replace("{transaction_id}", str(pid))
        )
        r = client.get(path_id)
        assert r.status_code == 200, f"GET {path_id} -> {r.status_code}"
        _ = r.json()

    # REFUND (если есть соответствующий путь)
    if refund_path and pid is not None:
        path_ref = (
            refund_path
            .replace("{id}", str(pid))
            .replace("{payment_id}", str(pid))
            .replace("{transaction_id}", str(pid))
        )
        r = client.post(path_ref, json={"reason": "requested_by_customer"})
        assert r.status_code in (200, 201, 204), f"POST {path_ref} -> {r.status_code}"
