import os
import re
from datetime import datetime, timedelta, timezone

import psycopg
from flask import Flask, jsonify
from psycopg.rows import dict_row

# Pruned to the Payment context (see PROVENANCE.md). Kept from the seed:
# the fail-fast DB connect, /health, validate_card and the card regexes,
# and the money/local_time filters. Payment tables arrive in M5.

DATABASE_URL = os.getenv("DATABASE_URL", "")
LOCAL_TZ = timezone(timedelta(hours=7))  # Bangkok, no daylight saving


def get_connection(database_url: str) -> psycopg.Connection:
    try:
        conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as error:
        raise SystemExit(
            f"Could not connect to the database at DATABASE_URL={database_url!r}\n"
            f"{error}\n"
            "Is Postgres running? Try: docker compose up db -d"
        ) from None
    return conn


CARD_NUMBER_RE = re.compile(r"^\d{13,19}$")
CVC_RE = re.compile(r"^\d{3,4}$")
EXPIRY_RE = re.compile(r"^(0[1-9]|1[0-2])/(\d{2})$")


def validate_card(card_number, expiry, cvc) -> str | None:
    """Returns an error message, or None if the (mocked) card looks valid -
    right shape and not expired, not a real Luhn/network check."""
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
    now = datetime.now(timezone.utc)
    if (year, month) < (now.year, now.month):
        return "card has expired"
    return None


def local_time(value: datetime) -> str:
    """For the HTML pages: Bangkok time, no seconds or offset clutter."""
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def create_app(database_url: str = DATABASE_URL) -> Flask:
    app = Flask(__name__)
    app.db = get_connection(database_url)
    app.add_template_filter(local_time, "local_time")
    app.add_template_filter(lambda satang: f"THB {satang / 100:,.2f}", "money")

    @app.get("/health")
    def health():
        try:
            with app.db.cursor() as cur:
                cur.execute("SELECT 1")
        except psycopg.Error:
            return jsonify(status="error", error="database unreachable"), 503
        
        return jsonify(
            status="ok",
            revision=os.getenv("APP_REVISION", "local"),
        )

    return app
