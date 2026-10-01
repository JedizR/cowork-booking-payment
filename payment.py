"""Payment domain: request checks, card checks, test-card outcomes, money (PMT-R*).

Pure functions only; the routes in app.py own the SQL and the transactions.
"""

import re
import secrets
from datetime import datetime
from urllib.parse import urlparse

from clock import LOCAL_TZ

BIGINT_MAX = 2**63 - 1
INTEGER_MAX = 2**31 - 1
MIN_AMOUNT_SATANG = 1000  # THB 10.00 (D1)

SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_sessions (
    id TEXT PRIMARY KEY,
    booking_reference TEXT NOT NULL UNIQUE,
    amount_satang BIGINT NOT NULL CHECK (amount_satang >= 1000),
    currency TEXT NOT NULL CHECK (currency = 'THB'),
    description TEXT NOT NULL,
    success_url TEXT NOT NULL,
    cancel_url TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'complete', 'expired')),
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_attempts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES payment_sessions (id),
    brand TEXT NOT NULL,
    last4 TEXT NOT NULL CHECK (last4 ~ '^[0-9]{4}$'),
    result TEXT NOT NULL CHECK (result IN ('succeeded', 'declined')),
    decline_code TEXT,
    attempted_at TIMESTAMPTZ NOT NULL
);
-- PMT-R12: at most one succeeded attempt per session, even if the app is wrong.
CREATE UNIQUE INDEX IF NOT EXISTS one_success_per_session
    ON payment_attempts (session_id) WHERE result = 'succeeded';
CREATE TABLE IF NOT EXISTS refunds (
    id TEXT PRIMARY KEY,
    payment_session_id TEXT NOT NULL REFERENCES payment_sessions (id),
    booking_reference TEXT NOT NULL,
    amount_satang BIGINT NOT NULL CHECK (amount_satang >= 1),
    reason TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (payment_session_id, attempt)
);
CREATE TABLE IF NOT EXISTS test_clock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    now_override TIMESTAMPTZ
);
"""


class Invalid(ValueError):
    """A request field failed its check; the message names the field (400)."""


def new_id(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(16)  # 128 random bits (D22, PMT-R02)


def money(satang: int) -> str:
    return f"THB {satang // 100:,}.{satang % 100:02d}"


def commission(net_satang: int) -> int:
    """20% of net, rounded half up to the satang (PMT-R17, D25)."""
    return (net_satang * 20 + 50) // 100


# --- JSON request checks ------------------------------------------------------------------------

def _text(body: dict, field: str, allow_empty=False) -> str:
    value = body.get(field)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise Invalid(f"{field} is required and must be a non-empty string")
    return value


def _int(body: dict, field: str, minimum: int, maximum: int, too_small: str) -> int:
    value = body.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise Invalid(f"{field} must be an integer")
    if not -BIGINT_MAX - 1 <= value <= maximum:
        raise Invalid(f"{field} is out of range")
    if value < minimum:
        raise Invalid(too_small)
    return value


def _url(body: dict, field: str) -> str:
    value = body.get(field)
    parsed = urlparse(value) if isinstance(value, str) else None
    if not parsed or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise Invalid(f"{field} must be an absolute http or https URL")
    return value


def parse_instant(value, field: str) -> datetime:
    """ISO 8601 with an offset, else Invalid (D2)."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise Invalid(f"{field} must be ISO 8601 with an offset")
    return parsed


def parse_session_request(body) -> dict:
    """PMT-R02 field checks for POST /payment-sessions."""
    if not isinstance(body, dict):
        raise Invalid("request body must be a JSON object")
    req = {
        "booking_reference": _text(body, "booking_reference"),
        "amount_satang": _int(body, "amount_satang", MIN_AMOUNT_SATANG, BIGINT_MAX,
                              "amount_satang must be at least 1000 (THB 10.00)"),
    }
    if body.get("currency") != "THB":
        raise Invalid("currency must be THB")
    req["currency"] = "THB"
    req["description"] = _text(body, "description", allow_empty=True)
    req["success_url"] = _url(body, "success_url")
    req["cancel_url"] = _url(body, "cancel_url")
    req["expires_at"] = parse_instant(body.get("expires_at"), "expires_at")
    return req


def parse_refund_request(body) -> dict:
    """PMT-R14 field checks for POST /refunds."""
    if not isinstance(body, dict):
        raise Invalid("request body must be a JSON object")
    return {
        "payment_session_id": _text(body, "payment_session_id"),
        "booking_reference": _text(body, "booking_reference"),
        "amount_satang": _int(body, "amount_satang", 1, BIGINT_MAX,
                              "amount_satang must be at least 1"),
        "reason": _text(body, "reason"),
        "attempt": _int(body, "attempt", 1, INTEGER_MAX, "attempt must be at least 1"),
    }


# --- Sessions -----------------------------------------------------------------------------------

def effective_status(row: dict, now: datetime) -> str:
    """An open session reads expired from the instant now reaches expires_at (PMT-R05)."""
    if row["status"] == "open" and now >= row["expires_at"]:
        return "expired"
    return row["status"]


def session_json(row: dict, now: datetime, public_url: str) -> dict:
    status = effective_status(row, now)
    return {
        "id": row["id"],
        "url": f"{public_url}/pay/{row['id']}",
        "status": status,
        "payment_status": "paid" if status == "complete" else "unpaid",
        "amount_satang": row["amount_satang"],
        "currency": row["currency"],
        "booking_reference": row["booking_reference"],
        "expires_at": row["expires_at"].astimezone(LOCAL_TZ).isoformat(),
    }


def refund_json(row: dict) -> dict:
    keys = ("id", "payment_session_id", "booking_reference", "amount_satang", "reason",
            "attempt", "status")
    return {k: row[k] for k in keys}


def success_redirect(row: dict) -> str:
    sep = "&" if "?" in row["success_url"] else "?"
    return f"{row['success_url']}{sep}session_id={row['id']}"


# --- Cards (kept from the seed: validate_card and its regexes) ----------------------------------

CARD_NUMBER_RE = re.compile(r"^\d{13,19}$")
CVC_RE = re.compile(r"^\d{3,4}$")
EXPIRY_RE = re.compile(r"^(0[1-9]|1[0-2])/(\d{2})$")


def validate_card(card_number, expiry, cvc, now: datetime) -> str | None:
    """Returns an error code, or None if the (mocked) card looks valid -
    right shape and not expired, not a real Luhn/network check. The month
    is the Bangkok month of clock.now() (PMT-R08)."""
    if not isinstance(card_number, str) or not CARD_NUMBER_RE.match(card_number):
        return "card_number must be 13-19 digits"
    if not isinstance(cvc, str) or not CVC_RE.match(cvc):
        return "cvc must be 3 or 4 digits"
    if not isinstance(expiry, str):
        return "expiry must be in MM/YY format"
    match = EXPIRY_RE.match(expiry)
    if match is None:
        return "expiry must be in MM/YY format"
    month, year = int(match.group(1)), 2000 + int(match.group(2))
    local = now.astimezone(LOCAL_TZ)
    if (year, month) < (local.year, local.month):
        return "card has expired"
    return None


# The seed's codes mapped to the PMT-R08 page text.
CARD_ERROR_TEXT = {
    "card_number must be 13-19 digits": "Card number must be 13 to 19 digits",
    "cvc must be 3 or 4 digits": "CVC must be 3 or 4 digits",
    "expiry must be in MM/YY format": "Expiry must be MM/YY",
    "card has expired": "The expiry date has passed",
}

# PMT-R09 / BRIEF 5.2. None means success. Every other number declines generic_decline.
TEST_CARDS = {
    "4242424242424242": None,
    "4000000000005126": None,  # its first refund fails (PMT-R16)
    "4000000000000002": "generic_decline",
    "4000000000009995": "insufficient_funds",
    "4000000000000069": "expired_card",
    "4000000000000119": "processing_error",
}
TEST_CARD_LIST = [
    ("4242424242424242", "success"),
    ("4000000000000002", "generic_decline"),
    ("4000000000009995", "insufficient_funds"),
    ("4000000000000069", "expired_card"),
    ("4000000000000119", "processing_error"),
    ("4000000000005126", "success; the first refund fails"),
]
DECLINE_TEXT = {  # PMT-T09
    "generic_decline": "Your card was declined.",
    "insufficient_funds": "Your card has insufficient funds.",
    "expired_card": "Your card has expired.",
    "processing_error": "An error occurred while processing your card. Try again.",
}
REFUND_FAILS_LAST4 = "5126"


def decline_code(card_number: str) -> str | None:
    return TEST_CARDS.get(card_number, "generic_decline")


def card_brand(card_number: str) -> str:
    # ponytail: first-digit guess only; this is a mock with no network lookup.
    return {"4": "visa", "5": "mastercard", "3": "amex"}.get(card_number[0], "card")
