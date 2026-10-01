"""Cowork Booking — Payment: mocked hosted checkout, sessions, attempts, refunds.

Contract: CONTRACT.md / openapi.yaml (tag contract-v1). Rules: PMT-R01..PMT-R20.
Payment calls no other service (PMT-R18).
"""

import hmac
import os
from datetime import timedelta
from functools import wraps
from math import ceil

import psycopg
from flask import Flask, flash, jsonify, redirect, render_template, request
from psycopg.rows import dict_row

import clock
import payment
from clock import LOCAL_TZ
from payment import Invalid

SEED_DEFAULT_SECRET = "dev-secret-key-not-for-production"


def require_env(name: str, min_len: int) -> str:
    """Fail fast on a missing or short secret (PMT-R01, PMT-R17, PMT-R19)."""
    value = os.getenv(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    if len(value) < min_len:
        raise SystemExit(f"{name} must be at least {min_len} characters")
    return value


def get_connection(database_url: str) -> psycopg.Connection:
    try:
        conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as error:
        raise SystemExit(
            f"Could not connect to the database at DATABASE_URL={database_url!r}\n"
            f"{error}\n"
            "Is Postgres running? Try: docker compose up db -d"
        ) from None
    with conn.transaction():
        # Both gunicorn workers run this at start; the lock stops a CREATE race.
        conn.execute("SELECT pg_advisory_xact_lock(8002)")
        conn.execute(payment.SCHEMA)
    return conn


def local_time(value) -> str:
    """Bangkok time the way Purchase writes it: "Mon 5 Oct, 10:04"."""
    if not value:
        return ""
    v = value.astimezone(LOCAL_TZ)
    return f"{v:%a} {v.day} {v:%b}, {v:%H:%M}"


def hhmm(value) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%H:%M")


def api_error(status: int, code: str, message: str):
    return jsonify(error={"code": code, "message": message}), status


def create_app(database_url: str | None = None) -> Flask:
    secret_key = require_env("SECRET_KEY", 32)
    if secret_key == SEED_DEFAULT_SECRET:
        raise SystemExit("SECRET_KEY is the public seed default")
    api_token = require_env("PAYMENT_API_TOKEN", 32)
    operator_password = require_env("OPERATOR_PASSWORD", 12)
    public_url = os.getenv("PUBLIC_URL", "http://localhost:8002").rstrip("/")

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_NAME="payment_session",  # carries flashes only, never card data
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=public_url.startswith("https"),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    # ponytail: one connection per worker (see gunicorn.conf.py), no reconnect.
    app.db = get_connection(database_url or os.getenv("DATABASE_URL", ""))
    app.add_template_filter(local_time, "local_time")
    app.add_template_filter(hhmm, "hhmm")
    app.add_template_filter(payment.money, "money")
    db = app.db

    def api(view):
        """Bearer PAYMENT_API_TOKEN, checked before anything else (PMT-R01)."""
        @wraps(view)
        def wrapper(*args, **kwargs):
            scheme, _, token = request.headers.get("Authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                token.encode("utf-8", "surrogateescape"), api_token.encode()
            ):
                return api_error(401, "unauthorized", "unauthorized")
            return view(*args, **kwargs)
        return wrapper

    def lock_session(session_id):
        return db.execute(
            "SELECT * FROM payment_sessions WHERE id = %s FOR UPDATE", (session_id,)
        ).fetchone()

    # --- API (Purchase only) -------------------------------------------------------------------

    @app.post("/payment-sessions")
    @api
    def create_session():
        try:
            req = payment.parse_session_request(request.get_json(silent=True))
        except Invalid as error:
            return api_error(400, "invalid_request", str(error))
        with db.transaction():
            now = clock.now()
            existing = db.execute(
                "SELECT * FROM payment_sessions WHERE booking_reference = %s FOR UPDATE",
                (req["booking_reference"],),
            ).fetchone()
            if existing is None:
                if req["expires_at"] <= now:
                    return api_error(400, "invalid_request", "expires_at must be in the future")
                existing = db.execute(
                    "INSERT INTO payment_sessions (id, booking_reference, amount_satang, currency,"
                    " description, success_url, cancel_url, expires_at, status, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open', %s)"
                    " ON CONFLICT (booking_reference) DO NOTHING RETURNING *",
                    (payment.new_id("ps_"), req["booking_reference"], req["amount_satang"],
                     req["currency"], req["description"], req["success_url"],
                     req["cancel_url"], req["expires_at"], now),
                ).fetchone()
                if existing is not None:
                    return jsonify(payment.session_json(existing, now, public_url)), 201
                # A parallel create for the same reference won; answer as a repeat.
                existing = db.execute(
                    "SELECT * FROM payment_sessions WHERE booking_reference = %s",
                    (req["booking_reference"],),
                ).fetchone()
            if (existing["amount_satang"], existing["currency"]) != (
                req["amount_satang"], req["currency"]
            ):
                return api_error(
                    409, "session_conflict",
                    f"{existing['booking_reference']} already has a payment session for "
                    f"{existing['amount_satang']} {existing['currency']}",
                )
            return jsonify(payment.session_json(existing, now, public_url)), 200

    @app.get("/payment-sessions/<session_id>")
    @api
    def get_session(session_id):
        row = db.execute("SELECT * FROM payment_sessions WHERE id = %s", (session_id,)).fetchone()
        if row is None:
            return api_error(404, "not_found", "payment session not found")
        return jsonify(payment.session_json(row, clock.now(), public_url))

    @app.post("/payment-sessions/<session_id>/expire")
    @api
    def expire_session(session_id):
        with db.transaction():
            row = lock_session(session_id)
            if row is None:
                return api_error(404, "not_found", "payment session not found")
            if row["status"] == "open":
                row = db.execute(
                    "UPDATE payment_sessions SET status = 'expired' WHERE id = %s RETURNING *",
                    (session_id,),
                ).fetchone()
            return jsonify(payment.session_json(row, clock.now(), public_url))

    @app.post("/refunds")
    @api
    def create_refund():
        try:
            req = payment.parse_refund_request(request.get_json(silent=True))
        except Invalid as error:
            return api_error(400, "invalid_request", str(error))
        with db.transaction():
            row = lock_session(req["payment_session_id"])
            if row is None:
                return api_error(404, "not_found", "payment session not found")
            if row["booking_reference"] != req["booking_reference"]:
                return api_error(409, "reference_mismatch",
                                 "booking_reference does not match the session")
            stored = db.execute(
                "SELECT * FROM refunds WHERE payment_session_id = %s AND attempt = %s",
                (row["id"], req["attempt"]),
            ).fetchone()
            if stored is not None:
                if stored["amount_satang"] != req["amount_satang"]:
                    return api_error(
                        409, "refund_conflict",
                        f"refund attempt {req['attempt']} already exists with a different amount",
                    )
                return jsonify(payment.refund_json(stored)), 200
            collected = row["amount_satang"] if row["status"] == "complete" else 0
            totals = db.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(amount_satang) FILTER"
                " (WHERE status = 'succeeded'), 0)::bigint AS refunded"
                " FROM refunds WHERE payment_session_id = %s",
                (row["id"],),
            ).fetchone()
            if totals["refunded"] + req["amount_satang"] > collected:
                return api_error(409, "refund_exceeds_collected", "refund exceeds amount collected")
            paid_last4 = db.execute(
                "SELECT last4 FROM payment_attempts WHERE session_id = %s AND result = 'succeeded'",
                (row["id"],),
            ).fetchone()["last4"]
            first_refund_fails = paid_last4 == payment.REFUND_FAILS_LAST4 and totals["n"] == 0
            refund = db.execute(
                "INSERT INTO refunds (id, payment_session_id, booking_reference, amount_satang,"
                " reason, attempt, status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING *",
                (payment.new_id("re_"), row["id"], row["booking_reference"],
                 req["amount_satang"], req["reason"], req["attempt"],
                 "failed" if first_refund_fails else "succeeded", clock.now()),
            ).fetchone()
            return jsonify(payment.refund_json(refund)), 201

    # --- Hosted page (Member's browser; the session id is the bearer link) -------------------

    def render_pay(row, state, status=200):
        now = clock.now()
        seconds_left = max(0, int((row["expires_at"] - now).total_seconds())) if row else 0
        return render_template(
            "pay.html", s=row, state=state, seconds_left=seconds_left,
            minutes_left=ceil(seconds_left / 60), test_cards=payment.TEST_CARD_LIST,
            success_href=payment.success_redirect(row) if row else None,
        ), status

    @app.get("/pay/<session_id>")
    def pay_page(session_id):
        row = db.execute("SELECT * FROM payment_sessions WHERE id = %s", (session_id,)).fetchone()
        if row is None:
            return render_pay(None, "not_found", 404)
        return render_pay(row, payment.effective_status(row, clock.now()))

    @app.post("/pay/<session_id>")
    def pay(session_id):
        # The attempt and the status update run in one transaction on the locked row (PMT-R12).
        with db.transaction():
            row = lock_session(session_id)
            if row is None:
                return render_pay(None, "not_found", 404)
            now = clock.now()
            state = payment.effective_status(row, now)
            if state == "complete":
                return redirect(payment.success_redirect(row), 303)
            if state == "expired":
                return render_pay(row, "expired", 409)
            number = request.form.get("card_number", "").replace(" ", "")
            code = payment.validate_card(number, request.form.get("expiry", "").strip(),
                                         request.form.get("cvc", "").strip(), now)
            if code:
                flash(payment.CARD_ERROR_TEXT[code], "form")
                return redirect(f"/pay/{session_id}", 303)
            decline = payment.decline_code(number)
            db.execute(
                "INSERT INTO payment_attempts (id, session_id, brand, last4, result,"
                " decline_code, attempted_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (payment.new_id("pa_"), session_id, payment.card_brand(number), number[-4:],
                 "declined" if decline else "succeeded", decline, now),
            )
            if decline:
                flash(payment.DECLINE_TEXT[decline], decline)
                return redirect(f"/pay/{session_id}", 303)
            db.execute(
                "UPDATE payment_sessions SET status = 'complete', paid_at = %s WHERE id = %s",
                (now, session_id),
            )
            return redirect(payment.success_redirect(row), 303)

    # --- Operator page (HTTP Basic, OPERATOR_PASSWORD) ----------------------------------------

    @app.get("/operator")
    def operator():
        auth = request.authorization
        password = (auth.password if auth else None) or ""
        if not password or not hmac.compare_digest(
            password.encode("utf-8", "surrogateescape"), operator_password.encode()
        ):
            return ("Operator password required", 401,
                    {"WWW-Authenticate": 'Basic realm="operator"'})
        now = clock.now()
        sessions = db.execute(
            "SELECT * FROM payment_sessions ORDER BY paid_at DESC NULLS LAST, created_at DESC"
        ).fetchall()
        for s in sessions:
            s["shown_status"] = payment.effective_status(s, now)
        attempts = db.execute(
            "SELECT a.*, s.booking_reference, s.cancel_url FROM payment_attempts a"
            " JOIN payment_sessions s ON s.id = a.session_id ORDER BY a.attempted_at DESC"
        ).fetchall()
        # cancel_url is the booking's page on Purchase: every booking reference links there.
        refunds = db.execute(
            "SELECT r.*, s.cancel_url FROM refunds r JOIN payment_sessions s"
            " ON s.id = r.payment_session_id ORDER BY r.created_at DESC"
        ).fetchall()
        follow_up = db.execute(
            "SELECT r.*, s.cancel_url FROM refunds r JOIN payment_sessions s"
            " ON s.id = r.payment_session_id WHERE r.status = 'failed' AND NOT EXISTS ("
            " SELECT 1 FROM refunds later WHERE later.payment_session_id = r.payment_session_id"
            " AND later.status = 'succeeded' AND later.attempt > r.attempt)"
            " ORDER BY r.created_at DESC"
        ).fetchall()
        collected = db.execute(
            "SELECT COALESCE(SUM(amount_satang), 0)::bigint AS v FROM payment_sessions"
            " WHERE status = 'complete'"
        ).fetchone()["v"]
        refunded = db.execute(
            "SELECT COALESCE(SUM(amount_satang), 0)::bigint AS v FROM refunds WHERE status = 'succeeded'"
        ).fetchone()["v"]
        net = collected - refunded
        totals = {"collected": collected, "refunded": refunded, "net": net,
                  "commission": payment.commission(net)}
        return render_template("operator.html", sessions=sessions, attempts=attempts,
                               refunds=refunds, follow_up=follow_up, totals=totals, now=now,
                               purchase=payment.origin(sessions[0]["success_url"]) if sessions else None,
                               decline_words=payment.DECLINE_WORDS, reason_words=payment.REASON_WORDS)

    # --- Ops ----------------------------------------------------------------------------------

    @app.get("/health")
    def health():
        try:
            db.execute("SELECT 1")
        except psycopg.Error:
            return jsonify(status="error", error="database unreachable"), 503
        return jsonify(status="ok", revision=os.getenv("APP_REVISION", "local"))

    @app.post("/_test/clock")
    def set_test_clock():
        if not clock.test_clock_enabled():
            return api_error(404, "not_found", "not found")
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or "now" not in body:
            return api_error(400, "invalid_request", "now is required (ISO 8601 or null)")
        value = None
        if body["now"] is not None:
            try:
                value = payment.parse_instant(body["now"], "now")
            except Invalid as error:
                return api_error(400, "invalid_request", str(error))
        db.execute(
            "INSERT INTO test_clock (id, now_override) VALUES (1, %s)"
            " ON CONFLICT (id) DO UPDATE SET now_override = EXCLUDED.now_override",
            (value,),
        )
        return jsonify(now=value.astimezone(LOCAL_TZ).isoformat() if value else None)

    return app
