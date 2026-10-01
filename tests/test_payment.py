"""Payment rules, one test name per rule (PMT-R01..PMT-R20)."""

import re
import subprocess
import sys
import threading

from conftest import AUTH, OPERATOR, STANDARD, new_session, pay, refund, set_clock

import payment
from app import create_app


def sid_of(client, **overrides):
    return new_session(client, **overrides).get_json()["id"]


# --- API auth and sessions ----------------------------------------------------------------------

def test_pmt_r01_missing_wrong_or_basic_token_gets_401_before_validation(client):
    assert client.post("/payment-sessions", json={"amount_satang": 5}).status_code == 401
    bad = AUTH["Authorization"][:-1] + "x"
    r = client.get("/payment-sessions/ps_x", headers={"Authorization": bad})
    assert r.status_code == 401 and r.get_json() == {
        "error": {"code": "unauthorized", "message": "unauthorized"}}
    token = AUTH["Authorization"].split()[1]
    assert client.get("/payment-sessions/ps_x", headers={"Authorization": f"Basic {token}"}).status_code == 401
    assert client.get("/payment-sessions/ps_x", headers={"Authorization": "Bearer é"}).status_code == 401
    assert client.get("/operator", auth=OPERATOR).get_data(as_text=True).count("data-session-id") == 0


def test_pmt_r01_r17_r19_start_refuses_missing_or_weak_secrets(monkeypatch):
    def start(**env):
        code = "from app import create_app; create_app()"
        full = {**__import__("os").environ, **env}
        return subprocess.run([sys.executable, "-c", code], env=full, capture_output=True, text=True)

    r = start(PAYMENT_API_TOKEN="")
    assert r.returncode != 0 and "PAYMENT_API_TOKEN is required" in r.stderr
    r = start(PAYMENT_API_TOKEN="x" * 31)
    assert "PAYMENT_API_TOKEN must be at least 32 characters" in r.stderr
    r = start(OPERATOR_PASSWORD="x" * 11)
    assert "OPERATOR_PASSWORD must be at least 12 characters" in r.stderr
    r = start(SECRET_KEY="dev-secret-key-not-for-production")
    assert "SECRET_KEY is the public seed default" in r.stderr
    r = start(SECRET_KEY="")
    assert "SECRET_KEY is required" in r.stderr


def test_pmt_r02_valid_request_creates_open_session_with_128_bit_id(client):
    r = new_session(client)
    body = r.get_json()
    assert r.status_code == 201
    assert re.fullmatch(r"ps_[A-Za-z0-9_-]{22}", body["id"])
    assert body["url"] == f"http://localhost:8002/pay/{body['id']}"
    assert (body["status"], body["payment_status"], body["amount_satang"], body["expires_at"]) == (
        "open", "unpaid", 45000, "2026-10-05T10:13:00+07:00")
    other = sid_of(client, booking_reference="BK-3HT8WD")
    assert other != body["id"]


def test_pmt_r02_invalid_fields_get_400_naming_the_field(client):
    cases = [
        ({"amount_satang": 999}, "amount_satang must be at least 1000 (THB 10.00)"),
        ({"amount_satang": 0}, "amount_satang must be at least 1000 (THB 10.00)"),
        ({"amount_satang": 9223372036854775808}, "amount_satang is out of range"),
        ({"amount_satang": 450.5}, "amount_satang must be an integer"),
        ({"amount_satang": "45000"}, "amount_satang must be an integer"),
        ({"currency": "USD"}, "currency must be THB"),
        ({"expires_at": "2026-10-05T10:13:00"}, "expires_at must be ISO 8601 with an offset"),
        ({"expires_at": "2026-10-05T10:00:00+07:00"}, "expires_at must be in the future"),
        ({"success_url": "/relative"}, "success_url must be an absolute http or https URL"),
    ]
    for override, message in cases:
        r = new_session(client, **override)
        assert r.status_code == 400, override
        assert r.get_json()["error"] == {"code": "invalid_request", "message": message}
    assert client.get("/operator", auth=OPERATOR).get_data(as_text=True).count("data-session-id") == 0


def test_pmt_r02_amount_of_exactly_thb_10_is_accepted(client):
    assert new_session(client, amount_satang=1000).status_code == 201
    sid = sid_of(client, amount_satang=1000)
    assert "THB 10.00" in client.get(f"/pay/{sid}").get_data(as_text=True)


def test_pmt_r03_repeat_returns_stored_session_and_other_amount_gets_409(client):
    first = new_session(client).get_json()
    again = new_session(client, expires_at="2026-10-05T10:20:00+07:00", description="new")
    assert again.status_code == 200 and again.get_json() == first
    r = new_session(client, amount_satang=30000)
    assert r.status_code == 409
    assert r.get_json()["error"] == {
        "code": "session_conflict",
        "message": "BK-7KQ2M9 already has a payment session for 45000 THB"}
    set_clock(client, "2026-10-05T10:14:00+07:00")
    late = new_session(client)
    assert late.status_code == 200 and late.get_json()["status"] == "expired"


def test_pmt_r04_amount_is_shown_and_collected_as_given(client):
    sid = sid_of(client, amount_satang=4000000)
    assert "THB 40,000.00" in client.get(f"/pay/{sid}").get_data(as_text=True)
    pay(client, sid)
    html = client.get("/operator", auth=OPERATOR).get_data(as_text=True)
    assert 'data-collected-satang="4000000"' in html


def test_pmt_r05_session_reads_expired_from_expires_at_but_paid_stays_paid(client):
    sid = sid_of(client)
    set_clock(client, "2026-10-05T10:12:59+07:00")
    assert client.get(f"/payment-sessions/{sid}", headers=AUTH).get_json()["status"] == "open"
    set_clock(client, "2026-10-05T10:13:00+07:00")
    body = client.get(f"/payment-sessions/{sid}", headers=AUTH).get_json()
    assert (body["status"], body["payment_status"]) == ("expired", "unpaid")
    r = client.get("/payment-sessions/ps_doesnotexist", headers=AUTH)
    assert r.status_code == 404 and r.get_json()["error"]["message"] == "payment session not found"

    set_clock(client, "2026-10-05T10:00:00+07:00")
    paid = sid_of(client, booking_reference="BK-3HT8WD")
    pay(client, paid)
    set_clock(client, "2026-10-05T10:20:00+07:00")
    body = client.get(f"/payment-sessions/{paid}", headers=AUTH).get_json()
    assert (body["status"], body["payment_status"]) == ("complete", "paid")


def test_pmt_r06_expire_open_session_then_pay_is_refused(client):
    sid = sid_of(client)
    r = client.post(f"/payment-sessions/{sid}/expire", headers=AUTH)
    assert r.status_code == 200 and r.get_json()["status"] == "expired"
    assert client.post(f"/payment-sessions/{sid}/expire", headers=AUTH).get_json()["status"] == "expired"
    r = pay(client, sid)
    assert r.status_code == 409
    assert "This payment session has expired. Nothing was charged." in r.get_data(as_text=True)
    assert client.post("/payment-sessions/ps_doesnotexist/expire", headers=AUTH).status_code == 404


def test_pmt_r06_expire_after_payment_returns_paid(client):
    sid = sid_of(client)
    pay(client, sid)
    body = client.post(f"/payment-sessions/{sid}/expire", headers=AUTH).get_json()
    assert (body["status"], body["payment_status"]) == ("complete", "paid")


# --- Hosted page --------------------------------------------------------------------------------

def test_pmt_r07_hosted_page_shows_amount_countdown_banner_and_ignores_query(client):
    sid = sid_of(client)
    set_clock(client, "2026-10-05T10:01:00+07:00")
    html = client.get(f"/pay/{sid}?error=Card+refused").get_data(as_text=True)
    for text in ("THB 450.00", "Booking BK-7KQ2M9",
                 "Pay by 10:13 (12 min left)", 'data-seconds-left="720"', "4000000000005126",
                 'name="card_number"', 'href="http://localhost:8001/bookings/BK-7KQ2M9"'):
        assert text in html, text
    # The description reads as sent; markup inside it (a no-wrap span) is free.
    assert "Meeting Room A, 2026-10-07 09:00-10:30" in re.sub(r"<[^>]+>", "", html)
    assert "Card refused" not in html
    assert "<title>Cowork Booking — Pay BK-7KQ2M9</title>" in html
    assert client.get("/pay/ps_doesnotexist").status_code == 404


def test_pmt_r07_line_item_is_the_description_exactly_as_purchase_sent_it(client):
    # Purchase owns the words and the time format; Payment never re-reads or reformats them.
    for ref, description in (("BK-1AAAAA", "Meeting Room A, Thu 8 Oct, 09:00\u201311:00"),
                             ("BK-2BBBBB", "The Long Boardroom on the Fourth Floor, East Wing, Thu 8 Oct, 09:00\u201313:00"),
                             ("BK-3CCCCC", "Meeting Room A, 2026-10-07 09:00-10:30")):
        sid = sid_of(client, booking_reference=ref, description=description)
        html = client.get(f"/pay/{sid}").get_data(as_text=True)
        assert f'<p class="checkout-item-name">{description}</p>' in html, description
        assert "Wednesday" not in html and "Thursday" not in html
        pay(client, sid)
        assert f'<dd class="end-for">{description}</dd>' in client.get(f"/pay/{sid}").get_data(as_text=True)
    html = client.get(f"/pay/{sid_of(client, booking_reference='BK-3HT8WD', description='Desk <b>7</b>')}").get_data(as_text=True)
    assert "Desk &lt;b&gt;7&lt;/b&gt;" in html
    assert '<p class="checkout-label">Booking BK-3HT8WD</p>' in html


def test_pmt_r08_card_field_errors_are_flashed_and_store_nothing(client):
    sid = sid_of(client)
    cases = [
        ({"card": "424242424242"}, "Card number must be 13 to 19 digits"),
        ({"card": "42424242424242424242"}, "Card number must be 13 to 19 digits"),
        ({"cvc": "12345"}, "CVC must be 3 or 4 digits"),
        ({"expiry": "13/28"}, "Expiry must be MM/YY"),
        ({"expiry": "00/28"}, "Expiry must be MM/YY"),
        ({"expiry": "09/26"}, "The expiry date has passed"),
    ]
    for kwargs, message in cases:
        r = pay(client, sid, **kwargs)
        assert r.status_code == 303 and r.location == f"/pay/{sid}"
        html = client.get(f"/pay/{sid}").get_data(as_text=True)
        assert message in html and "data-decline-code" not in html
    assert "data-attempt-result" not in client.get("/operator", auth=OPERATOR).get_data(as_text=True)
    # The current month passes; spaces in the number are removed.
    r = pay(client, sid, card="4242 4242 4242 4242", expiry="10/26")
    assert r.status_code == 303 and "session_id=" in r.location


def test_pmt_r09_r10_test_cards_decide_outcome_and_decline_keeps_session_open(client):
    sid = sid_of(client)
    for card, code, text in [
        ("4000000000000002", "generic_decline", "Your card was declined."),
        ("4000000000009995", "insufficient_funds", "Your card has insufficient funds."),
        ("4000000000000069", "expired_card", "Your card has expired."),
        ("4000000000000119", "processing_error", "An error occurred while processing your card. Try again."),
        ("4111111111111111", "generic_decline", "Your card was declined."),
        ("4000000000002", "generic_decline", "Your card was declined."),
    ]:
        r = pay(client, sid, card=card)
        assert r.status_code == 303 and r.location == f"/pay/{sid}"
        html = client.get(f"/pay/{sid}").get_data(as_text=True)
        assert f'data-decline-code="{code}">{text}</p>' in html
        assert client.get(f"/payment-sessions/{sid}", headers=AUTH).get_json()["status"] == "open"
    r = client.post(f"/pay/{sid}", data={"card_number": "4242424242424242", "expiry": "12/28",
                                         "cvc": "123", "force_failure": "true"})
    assert r.status_code == 303
    assert r.location == f"http://localhost:8001/bookings/BK-7KQ2M9/return?session_id={sid}"
    body = client.get(f"/payment-sessions/{sid}", headers=AUTH).get_json()
    assert (body["status"], body["payment_status"]) == ("complete", "paid")


def test_pmt_r11_no_attempt_at_or_after_expires_at(client):
    sid = sid_of(client)
    set_clock(client, "2026-10-05T10:13:00+07:00")
    r = pay(client, sid)
    html = r.get_data(as_text=True)
    assert r.status_code == 409
    assert "This payment session has expired. Nothing was charged." in html and "Back to your booking" in html
    assert "data-attempt-result" not in client.get("/operator", auth=OPERATOR).get_data(as_text=True)


def test_pmt_r11_attempt_one_second_before_expiry_is_accepted(client):
    sid = sid_of(client)
    set_clock(client, "2026-10-05T10:12:59+07:00")
    assert pay(client, sid).status_code == 303
    assert client.get(f"/payment-sessions/{sid}", headers=AUTH).get_json()["payment_status"] == "paid"


def test_pmt_r12_double_click_on_two_workers_charges_once(app):
    sid = sid_of(app.test_client())
    second = create_app()  # a second worker with its own connection
    results = []
    barrier = threading.Barrier(2)

    def click(a):
        barrier.wait()
        results.append(pay(a.test_client(), sid).status_code)

    threads = [threading.Thread(target=click, args=(a,)) for a in (app, second)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    second.db.close()
    assert results == [303, 303]
    html = app.test_client().get("/operator", auth=OPERATOR).get_data(as_text=True)
    assert html.count('data-attempt-result="succeeded"') == 1
    assert 'data-collected-satang="45000"' in html


def test_pmt_r12_pay_on_complete_session_stores_no_attempt(client):
    sid = sid_of(client)
    pay(client, sid)
    r = pay(client, sid, card="4000000000005126")
    assert r.status_code == 303 and "session_id=" in r.location
    html = client.get("/operator", auth=OPERATOR).get_data(as_text=True)
    assert html.count("data-attempt-result") == 1
    assert "visa ending 4242" in html


def test_pmt_r13_only_brand_and_last4_are_stored(app, client):
    sid = sid_of(client)
    pay(client, sid, card="4000000000000002", expiry="12/28", cvc="987")
    pay(client, sid, card="4242424242424242", expiry="11/29", cvc="123")
    rows = app.db.execute("SELECT * FROM payment_attempts ORDER BY attempted_at").fetchall()
    assert {(r["brand"], r["last4"]) for r in rows} == {("visa", "0002"), ("visa", "4242")}
    dump = str(app.db.execute("SELECT * FROM payment_attempts").fetchall())
    for secret in ("4000000000000002", "4242424242424242", "987", "11/29", "12/28"):
        assert secret not in dump


# --- Refunds ------------------------------------------------------------------------------------

def test_pmt_r14_refund_repeat_returns_stored_result_and_conflicts_get_409(client):
    sid = sid_of(client)
    pay(client, sid)
    first = refund(client, sid)
    assert first.status_code == 201 and first.get_json()["status"] == "succeeded"
    assert re.fullmatch(r"re_[A-Za-z0-9_-]{22}", first.get_json()["id"])
    again = refund(client, sid)
    assert again.status_code == 200 and again.get_json() == first.get_json()
    r = refund(client, sid, amount=20000)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "refund_conflict"
    r = refund(client, sid, ref="BK-3HT8WD", attempt=2)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "reference_mismatch"
    for bad in ({"attempt": 0}, {"amount": 0}, {"attempt": 2147483648}, {"amount": 9223372036854775808}):
        assert refund(client, sid, **bad).status_code == 400
    assert refund(client, "ps_doesnotexist").status_code == 404
    assert refund(client, sid, attempt=2, amount=0).status_code == 400


def test_pmt_r14_identical_refunds_together_get_one_201_and_one_200(app):
    sid = sid_of(app.test_client())
    pay(app.test_client(), sid)
    second = create_app()
    results = []
    barrier = threading.Barrier(2)

    def send(a):
        barrier.wait()
        r = refund(a.test_client(), sid)
        results.append((r.status_code, r.get_json()["id"]))

    threads = [threading.Thread(target=send, args=(a,)) for a in (app, second)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    second.db.close()
    assert sorted(code for code, _ in results) == [200, 201]
    assert results[0][1] == results[1][1]


def test_pmt_r15_refunds_never_exceed_collected(client):
    sid = sid_of(client)
    pay(client, sid)
    assert refund(client, sid, amount=20000, attempt=1).get_json()["status"] == "succeeded"
    assert refund(client, sid, amount=25000, attempt=2).status_code == 201
    r = refund(client, sid, amount=1, attempt=3)
    assert r.status_code == 409 and r.get_json()["error"] == {
        "code": "refund_exceeds_collected", "message": "refund exceeds amount collected"}
    unpaid = sid_of(client, booking_reference="BK-3HT8WD")
    assert refund(client, unpaid, ref="BK-3HT8WD").status_code == 409


def test_pmt_r16_card_5126_first_refund_fails_then_retry_succeeds(client):
    sid = sid_of(client, booking_reference="BK-3HT8WD", amount_satang=100000)
    pay(client, sid, card="4000000000005126")
    first = refund(client, sid, amount=100000, ref="BK-3HT8WD")
    assert first.status_code == 201 and first.get_json()["status"] == "failed"
    assert refund(client, sid, amount=100000, ref="BK-3HT8WD").get_json()["status"] == "failed"
    html = client.get("/operator", auth=OPERATOR).get_data(as_text=True)
    assert 'data-follow-up="BK-3HT8WD"' in html and "needs manual follow-up" in html
    second = refund(client, sid, amount=100000, ref="BK-3HT8WD", attempt=2)
    assert second.status_code == 201 and second.get_json()["status"] == "succeeded"
    body = client.get(f"/payment-sessions/{sid}", headers=AUTH).get_json()
    assert (body["status"], body["payment_status"]) == ("complete", "paid")
    html = client.get("/operator", auth=OPERATOR).get_data(as_text=True)
    assert "data-follow-up" not in html
    assert 'data-refund-attempt="1" data-refund-status="failed"' in html
    assert 'data-refund-attempt="2" data-refund-status="succeeded"' in html


# --- Operator, cookies, clock -------------------------------------------------------------------

def test_pmt_r17_operator_totals_and_commission_rounding(client):
    sid = sid_of(client, booking_reference="BK-9MZ4RC", amount_satang=1000)
    pay(client, sid)
    refund(client, sid, amount=3, ref="BK-9MZ4RC")
    html = client.get("/operator", auth=OPERATOR).get_data(as_text=True)
    for marker in ('data-collected-satang="1000"', 'data-refunded-satang="3"',
                   'data-net-satang="997"', 'data-commission-satang="199"', "THB 9.97", "THB 1.99",
                   "Estimated platform commission (20% of net)"):
        assert marker in html, marker


def test_pmt_r17_operator_links_back_to_purchase_and_shows_words_first(client):
    sid = sid_of(client, booking_reference="BK-3HT8WD", amount_satang=100000,
                 cancel_url="http://localhost:8001/bookings/BK-3HT8WD")
    pay(client, sid, card="4000000000009995")
    pay(client, sid, card="4000000000005126")
    refund(client, sid, amount=100000, ref="BK-3HT8WD", reason="operator_cancel")
    html = client.get("/operator", auth=OPERATOR).get_data(as_text=True)
    # Links come from the URLs Purchase sent (browser navigation only, PMT-R18).
    assert html.count('href="http://localhost:8001/bookings/BK-3HT8WD"') >= 4
    for href in ("/dashboard", "/operator/bookings", "/operator/spaces", "/operator/members"):
        assert f'href="http://localhost:8001{href}"' in html, href
    assert "Retry it from" in html and 'href="http://localhost:8001/operator/bookings?status=flagged">Purchase: All bookings</a>' in html
    assert "Insufficient funds" in html and "insufficient_funds" in html
    assert "Operator cancelled" in html and "Mon 5 Oct, 10:00" in html
    assert "<h1 class=\"page-title\">Payment totals</h1>" in html
    # The tabs read as Purchase's: Dashboard, Bookings, Spaces, Members, then this page, Payments.
    assert '<a href="/operator" aria-current="page">Payments</a>' in html
    assert 'class="brand" href="http://localhost:8001/"' in html
    # Purchase's bar: My bookings and Log out (a POST to Purchase, a browser form, not a call: PMT-R18).
    assert 'href="http://localhost:8001/bookings/mine">My bookings</a>' in html
    assert '<form method="post" action="http://localhost:8001/logout"><button' in html


def test_pmt_r17_operator_page_needs_the_password(client):
    for auth in (None, ("operator", "wrong-password-123"), ("operator", "passwörd")):
        r = client.get("/operator", auth=auth)
        assert r.status_code == 401 and r.headers["WWW-Authenticate"].startswith("Basic")
        # A cancelled prompt lands on a page that says why and offers the prompt again; no data.
        html = r.get_data(as_text=True)
        assert "Operator password required" in html and 'href="/operator">Enter password</a>' in html
        assert "data-collected-satang" not in html
    assert client.get("/operator", auth=("anyone", OPERATOR[1])).status_code == 200


def test_pmt_r19_flash_cookie_is_httponly_lax_and_holds_no_card_data(client):
    sid = sid_of(client)
    r = pay(client, sid, card="4000000000000002")
    cookie = r.headers["Set-Cookie"]
    assert cookie.startswith("payment_session=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Secure" not in cookie and "4000000000000002" not in cookie


def test_pmt_r20_test_clock_is_404_unless_enabled_and_rejects_naive_time(client, monkeypatch):
    assert client.post("/_test/clock", json={"now": "2026-10-05T10:13:00"}).status_code == 400
    r = client.post("/_test/clock", json={"now": None})
    assert r.status_code == 200 and r.get_json() == {"now": None}
    for flag in ("false", "1"):
        monkeypatch.setenv("TEST_CLOCK_ENABLED", flag)
        assert client.post("/_test/clock", json={"now": "2026-10-05T10:13:00+07:00"}).status_code == 404
