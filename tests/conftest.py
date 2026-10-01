import os

import psycopg
import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:55462/postgres")
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-0123456789abcdef")
os.environ.setdefault("PAYMENT_API_TOKEN", "test-payment-api-token-0123456789abcdef")
os.environ.setdefault("OPERATOR_PASSWORD", "test-operator-password")

from app import create_app  # noqa: E402

TOKEN = os.environ["PAYMENT_API_TOKEN"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}
OPERATOR = ("operator", os.environ["OPERATOR_PASSWORD"])
NOW = "2026-10-05T10:00:00+07:00"
STANDARD = {
    "booking_reference": "BK-7KQ2M9", "amount_satang": 45000, "currency": "THB",
    "description": "Meeting Room A, 2026-10-07 09:00-10:30",
    "success_url": "http://localhost:8001/bookings/BK-7KQ2M9/return",
    "cancel_url": "http://localhost:8001/bookings/BK-7KQ2M9",
    "expires_at": "2026-10-05T10:13:00+07:00",
}


@pytest.fixture
def app(monkeypatch):
    """A fresh schema per test, with the test clock on and set to 2026-10-05 10:00 Bangkok."""
    monkeypatch.setenv("TEST_CLOCK_ENABLED", "true")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS refunds, payment_attempts, payment_sessions, test_clock")
    app = create_app()
    app.test_client().post("/_test/clock", json={"now": NOW})
    yield app
    app.db.close()


@pytest.fixture
def client(app):
    return app.test_client()


def set_clock(client, now):
    assert client.post("/_test/clock", json={"now": now}).status_code == 200


def new_session(client, **overrides):
    return client.post("/payment-sessions", json={**STANDARD, **overrides}, headers=AUTH)


def pay(client, session_id, card="4242424242424242", expiry="12/28", cvc="123"):
    return client.post(f"/pay/{session_id}", data={"card_number": card, "expiry": expiry, "cvc": cvc})


def refund(client, session_id, amount=45000, attempt=1, ref="BK-7KQ2M9", reason="member_cancel"):
    return client.post("/refunds", headers=AUTH, json={
        "payment_session_id": session_id, "booking_reference": ref,
        "amount_satang": amount, "reason": reason, "attempt": attempt})
