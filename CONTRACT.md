# Contract: Purchase → Payment

| Field | Value |
|---|---|
| Provider | Payment (`cowork-booking-payment`), port 8002 |
| Consumer | Purchase (`cowork-booking-purchase`), through `payment_client.py` only |
| Other users | The Member's browser (hosted page `/pay/<id>`); the Operator (page `/operator`); the e2e suite |
| State | proposed (M2 draft). Becomes agreed at M4 sign-off (tag `contract-v1`), verified by the M6 e2e run |
| OpenAPI | [openapi/payment.yaml](openapi/payment.yaml) |
| Decisions | D1, D2, D8, D10, D12, D13, D14, D18, D19, D22, D27, D28; ADR-0004, ADR-0014, ADR-0018, ADR-0019, ADR-0020 |

## 1. Purpose and parties

Purchase asks Payment to "Collect this agreed amount" ([extraction site]). Payment owns payment outcomes. Purchase interprets the result.

- **Payment does:** take the amount as given, show a hosted mock checkout page, record attempts and refunds, report outcomes when Purchase asks (PMT-R04, PMT-R18).
- **Payment never:** prices a booking, sees coverage, sets a booking status, or calls another service. There are no webhooks (ADR-0004, PMT-R18).
- **Purchase does:** send the agreed price (PUR-R17, PUR-R23), read the session and decide (PUR-R24, PUR-R25), expire a session before a held cancel (PUR-R31), and request refunds (PUR-R32, PUR-R33).
- **Plan and free bookings never reach Payment** (PUR-R20, section 5.5).

No real money moves. The card outcomes come from the test-card table (ADR-0018, PMT-R09).

## 2. Base URLs and authentication

| Setting | Example | Used by |
|---|---|---|
| `PAYMENT_INTERNAL_URL` (Purchase env) | `http://payment:8000` | Purchase, for every API call |
| `PUBLIC_URL` (Payment env) | `http://localhost:8002` | Payment, to build the session `url` for the browser |
| `PAYMENT_PUBLIC_URL` (Purchase env) | `http://localhost:8002` | Purchase, to send the browser to `PAYMENT_PUBLIC_URL/pay/<stored session id>` (also `payment_url` in its JSON) and for the "Payment totals" link; Purchase never redirects to the `url` in Payment's answer (PUR-R23) |

Authentication (ADR-0019):

- **API** (`/payment-sessions*`, `/refunds`): send `Authorization: Bearer <PAYMENT_API_TOKEN>`. A missing token, a wrong token or another scheme gets 401 before any validation or lookup, and nothing changes (PMT-R01). Payment compares in constant time. Payment refuses to start when `PAYMENT_API_TOKEN` is unset, empty or shorter than 32 characters (PMT-R01, ADR-0019).
- **Hosted page** (`/pay/<id>`): no login and no token. The 128-bit session id is the bearer link (PMT-R07, PMT-Q05).
- **Operator page** (`/operator`): HTTP Basic, user `operator`, password `OPERATOR_PASSWORD`. Payment checks only the password and refuses to start when `OPERATOR_PASSWORD` is unset, empty or shorter than 12 characters (PMT-R17).
- `/health` and `/_test/clock` need no credentials.

## 3. Conventions

- JSON in and out, `Content-Type: application/json`.
- Money: integer satang, field names end in `_satang`, currency always `"THB"` (D1, PUR-R18). 45000 is THB 450.00.
- Times: ISO 8601 with an offset. Payment answers in Bangkok time, `+07:00` (D2). A time without an offset gets 400.
- IDs: prefixes session `ps_`, attempt `pa_`, refund `re_` (D22). The session id is `ps_` + `secrets.token_urlsafe(16)`, 128 random bits (PMT-R02, PMT-T02, ADR-0019): it is the bearer link to the hosted page, so it must be unguessable. `pa_` and `re_` ids need only be unique.
- Unknown request fields are ignored. Consumers ignore unknown response fields.
- Errors always have this shape. Only `GET /health` keeps the seed shape, `{"status": "error", "error": "database unreachable"}` (section 4.8):

```json
{"error": {"code": "invalid_request", "message": "amount_satang must be at least 1000 (THB 10.00)"}}
```

The rule rows in RULES.md quote only the text, for example `{"error": "unauthorized"}`. That text is `error.message`.

| Status | error.code | When | Rule |
|---|---|---|---|
| 400 | `invalid_request` | A field is missing, has the wrong type or is out of range. The message names the field. | PMT-R02, PMT-R14 |
| 401 | `unauthorized` | No token, a wrong token, or the wrong scheme | PMT-R01 |
| 404 | `not_found` | Unknown session id, message "payment session not found" | PMT-R05, PMT-R06, PMT-R14 |
| 409 | `session_conflict` | The booking reference already has a session with another amount | PMT-R03 |
| 409 | `refund_conflict` | The refund attempt already exists with another amount | PMT-R14 |
| 409 | `reference_mismatch` | The refund's booking_reference is not the session's | PMT-R14 |
| 409 | `refund_exceeds_collected` | Refunded total plus this amount is above the amount collected | PMT-R15 |

## 4. Operations

### 4.1 Session object

Every session answer has this shape (PMT-R02, PMT-R05).

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `ps_` + 22 characters, e.g. `ps_Q7mZ3xK9vT2bN8rL4wYc1A` |
| `url` | string | The hosted page: Payment `PUBLIC_URL` + `/pay/<id>` |
| `status` | string | `open`, `complete` or `expired` (PMT-R05) |
| `payment_status` | string | `unpaid` or `paid`. `paid` only when `complete` |
| `amount_satang` | integer | The agreed price as sent, fixed for life (PMT-R04) |
| `currency` | string | `THB` |
| `booking_reference` | string | As sent, e.g. `BK-7KQ2M9` |
| `expires_at` | string | ISO 8601, `+07:00` |

A session reads `expired` from the instant `clock.now()` reaches `expires_at`, even before anything writes it (PMT-R05). `complete` and `expired` are final. Refunds never change `status` or `payment_status` (PMT-R16).

### 4.2 POST /payment-sessions

Create the checkout for one held booking. Purchase calls it after the booking insert commits (PUR-R23).

Request:

| Field | Type | Required | Constraints | Rule |
|---|---|---|---|---|
| `booking_reference` | string | yes | Non-empty. Purchase sends `BK-` + 6 symbols (PUR-R29) | PMT-R02 |
| `amount_satang` | integer | yes | At least 1000 (THB 10.00), at most 9223372036854775807. A float or a string gets 400 | PMT-R02, PMT-R04 |
| `currency` | string | yes | Exactly `THB` | PMT-R02 |
| `description` | string | yes | Space name and Bangkok time only, e.g. `Meeting Room A, 2026-10-07 09:00-10:30`. Never a name or email (PUR-R23) | PMT-R02 |
| `success_url` | string | yes | Absolute http or https URL. Payment appends `?session_id=<id>` | PMT-R02, PMT-R10 |
| `cancel_url` | string | yes | Absolute http or https URL. The page's Back link | PMT-R02, PMT-R07 |
| `expires_at` | string | yes | ISO 8601 with an offset. For a new session it must be after `clock.now()`. Purchase sends `hold_expires_at` minus 2 min (D12) | PMT-R02, PMT-R11 |

Responses:

| Status | Body | When |
|---|---|---|
| 201 | Session object, `status` open | New booking reference, valid request |
| 200 | The stored session, unchanged, in whatever status it has | Repeat: the reference has a session with the same `amount_satang` and `currency`. Other fields are ignored |
| 400 | Error `invalid_request` | Any field invalid. Nothing stored |
| 401 | Error `unauthorized` | Token check fails. Checked first |
| 409 | Error `session_conflict` | The reference has a session with another amount. Nothing changes |

Order of checks: token (401), fields (400), then the repeat check (200 or 409), then insert (201). The future check on `expires_at` applies only to a new session, so a repeat after `expires_at` still gets 200 with `status` expired (PMT-R03).

Idempotency: natural key `booking_reference`, UNIQUE in Payment (ADR-0014). No Idempotency-Key header.

Implements: PMT-R01, PMT-R02, PMT-R03, PMT-R04, PMT-R18. Consumer side: PUR-R17, PUR-R21, PUR-R23, PUR-R40.

### 4.3 GET /payment-sessions/{id}

Read one session. Purchase calls it whenever it reconciles a held booking. The reconcile points (PUR-R24): the booking page and its return URL, GET /api/bookings/<ref>, My bookings (the page and GET /api/bookings/mine), the cancel confirm screen, the pre-insert sweep, the Member's own lapsed holds (PUR-R39), and the Operator's Reconcile of one booking or of all held. The POST cancel uses expire in place of this read (PUR-R31).

| Status | Body | When |
|---|---|---|
| 200 | Session object | Known id |
| 401 | Error `unauthorized` | Token check fails |
| 404 | Error `not_found`, "payment session not found" | Unknown id |

Idempotency: a read. It writes nothing.

Implements: PMT-R01, PMT-R05, PMT-R10, PMT-R11. Consumer side: PUR-R24, PUR-R25.

### 4.4 POST /payment-sessions/{id}/expire

End an open session now. Purchase calls it first when a held booking is cancelled (PUR-R31, D18). No request body.

| Status | Body | When |
|---|---|---|
| 200 | Session object, `status` expired, `payment_status` unpaid | The session was open. It is now expired |
| 200 | Session object, `status` complete, `payment_status` paid | The session was already paid. It stays paid |
| 200 | Session object, `status` expired | Already expired. Nothing changes |
| 401 | Error `unauthorized` | Token check fails |
| 404 | Error `not_found` | Unknown id |

The expire and a payment attempt lock the same session row (`SELECT … FOR UPDATE`). Exactly one of them wins: never expired and charged (PMT-R06).

Idempotency: natural key the session id. A repeat returns the final state and changes nothing.

Implements: PMT-R01, PMT-R06, PMT-R12. Consumer side: PUR-R31.

### 4.5 POST /refunds

Return money from a paid session. Purchase calls it only after the grant revoke succeeded (PUR-R32, PUR-Q10), or at once for the full refunds of D14 (PUR-R25).

Request:

| Field | Type | Required | Constraints | Rule |
|---|---|---|---|---|
| `payment_session_id` | string | yes | An existing `ps_` id, else 404 | PMT-R14 |
| `booking_reference` | string | yes | Must equal the session's reference, else 409 | PMT-R14 |
| `amount_satang` | integer | yes | At least 1, BIGINT range. Refunded total plus this amount must not pass the amount collected | PMT-R14, PMT-R15 |
| `reason` | string | yes | Non-empty. Purchase sends `member_cancel`, `operator_cancel`, `amount_mismatch` or `slot_unavailable` (PUR-T29) | PMT-R14 |
| `attempt` | integer | yes | 1 to 2147483647. Purchase sends 1, then attempt+1 only on the Operator's Retry after a stored `failed` (PUR-R33) | PMT-R14 |

Refund object:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `re_` + 22 characters |
| `payment_session_id`, `booking_reference`, `amount_satang`, `reason`, `attempt` | as sent | |
| `status` | string | `succeeded` or `failed`, final at once (PMT-R16) |

Responses:

| Status | Body | When |
|---|---|---|
| 201 | Refund object | New `(payment_session_id, attempt)` |
| 200 | The stored refund object, even if it failed | Repeat of the same key with the same amount. Nothing else happens |
| 400 | Error `invalid_request` | A field is invalid, e.g. `attempt` 0 or `amount_satang` 0 |
| 401 | Error `unauthorized` | Token check fails. Checked first |
| 404 | Error `not_found` | Unknown session |
| 409 | Error `reference_mismatch` | `booking_reference` is not the session's |
| 409 | Error `refund_conflict` | Same key, different amount |
| 409 | Error `refund_exceeds_collected` | Over the balance. An unpaid session collected 0 |

Order of checks: token (401), fields (400), session (404), reference (409); then, with the session row locked (`SELECT ... FOR UPDATE`), the repeat key (200 or 409), the balance (409) and the insert (201), all in one transaction (PMT-R14, PMT-R15). Locking before the repeat key means two identical requests that arrive together get one 201 and one 200 with the same `re_` id, never a 409.

Outcome: for a session paid with test card 4000000000005126, the first refund stored for that session fails and later ones succeed. Every other paid session refunds successfully (PMT-R16). There is no pending state.

Idempotency: natural key `(payment_session_id, attempt)`, UNIQUE in Payment (ADR-0014).

Implements: PMT-R01, PMT-R14, PMT-R15, PMT-R16. Consumer side: PUR-R25, PUR-R32, PUR-R33.

### 4.6 GET /pay/{id} and POST /pay/{id} (browser)

The hosted mock checkout (ADR-0018). Purchase only redirects the browser to the session `url`.

GET `/pay/<id>`:

| Session | Status | Page |
|---|---|---|
| open | 200 | "THB 450.00", "Booking BK-7KQ2M9", the description, "Pay by 10:13 (12 min left)" with a `data-seconds-left` attribute, the seconds from `clock.now()` to `expires_at` at render time (a few lines of inline script count it down and, at zero, disable Pay and show "Time to pay has run out"; the browser clock is never read; the server check decides), the test-mode banner with the 6 test cards, the card form, Pay, and Back (a link to `cancel_url`). After a decline or a card-field error, the flashed reason shows here once |
| complete | 200 | "Paid" and a "Return to booking" link to `success_url?session_id=<id>`. No form |
| expired, or `clock.now()` at or after `expires_at` | 200 | "This payment session has expired. Nothing was charged." and "Back to your booking" (a link to `cancel_url`). No form |
| unknown id | 404 | Not-found page. No booking data |

POST `/pay/<id>`, form fields `card_number`, `expiry` (MM/YY), `cvc`:

| Case | Status | Result |
|---|---|---|
| Success card | 303 | `Location: <success_url>?session_id=<id>`. Session complete and paid |
| Decline card | 303 | `Location: /pay/<id>`. The attempt is stored; the reason (PMT-T09) is flashed and the GET shows it inside an element `data-decline-code="<code>"`. Session stays open for another try |
| Card fields fail the check (PMT-R08) | 303 | `Location: /pay/<id>`. The message is flashed, e.g. "Card number must be 13 to 19 digits", and the GET shows it with no `data-decline-code` element. No attempt stored |
| At or after `expires_at`, or session expired | 409 | The same expired page as the GET: "This payment session has expired. Nothing was charged." and "Back to your booking" (a link to `cancel_url`). No attempt stored, no charge |
| Session already complete | 303 | To `success_url?session_id=<id>`. No attempt stored, no charge (PMT-R12) |
| Unknown id | 404 | Not-found page |

Order of checks for POST: unknown id (404); complete (303 to `success_url`, no attempt); expired or at or after `expires_at` (409); card fields (303 with the flash); then the attempt, inside the session lock (PMT-R12).

A decline and a card-field error follow post, redirect, get: 303 back to `/pay/<id>`, and the GET shows the flashed reason inline, carried by the `payment_session` cookie (PMT-R10, PMT-R19, D28). No query string carries it, and a reload never re-posts the card fields. `requests.Session` follows the redirect, so the marker stays testable. The 409 is the status for PMT-R11.

After any answer the card fields are empty. The number, expiry and CVC never appear in a URL, a flash, a log line or the database. Only brand and last4 are stored (PMT-R13, ADR-0020).

Implements: PMT-R07, PMT-R08, PMT-R09, PMT-R10, PMT-R11, PMT-R12, PMT-R13, PMT-R19.

### 4.7 GET /operator (Operator)

HTTP Basic, user `operator`. Without the right password: 401 with `WWW-Authenticate: Basic`. The page lists sessions (booking_reference, amount, status, paid_at), attempts (booking_reference, brand and last4 only, result, decline code, attempted_at) and refunds (booking_reference, attempt, amount, reason, status, created_at), each in a fixed order: sessions newest paid_at first, then unpaid sessions newest created first; attempts newest attempted_at first; refunds newest created_at first. paid_at, attempted_at and the refund's created_at are business times written from `clock.now()` in the pay or refund transaction, never a database default (D27), so a week can be summed by hand under the test clock too. The page shows no space per session: the host share per room is a manual join with Purchase's all-bookings list by booking reference (BUSINESS_MODEL.md section 6). It shows collected, refunded, net and "estimated platform commission (20% of net)", all-time (PMT-Q03). Each failed refund shows "needs manual follow-up" with the text "Retry it from Purchase: All bookings" until a later attempt for the same session succeeds (PMT-R17). Markers are in section 8.

### 4.8 GET /health and POST /_test/clock

- `GET /health`: 200 `{"status": "ok", "revision": "<APP_REVISION>"}`; 503 `{"status": "error", "error": "database unreachable"}`.
- `POST /_test/clock` `{"now": "2026-10-05T10:13:00+07:00"}` or `{"now": null}`: 200 `{"now": "2026-10-05T10:13:00+07:00"}` (or `{"now": null}`) only when `TEST_CLOCK_ENABLED` is exactly `true`. Otherwise 404 and nothing stored, and `clock.now()` never reads `test_clock` (PMT-R20, D27, ADR-0013). A `now` without an offset gets 400 and nothing is stored. There is no other test hook: a `force_failure` field is ignored.

## 5. Examples

Standard data: clock 2026-10-05 10:00 Bangkok, booking BK-7KQ2M9 (Member A, Meeting Room A, 2026-10-07 09:00-10:30, THB 450.00), hold until 10:15, session expires 10:13. Every API request carries `Authorization: Bearer <PAYMENT_API_TOKEN>`.

### 5.1 Success

Purchase creates the session at 10:00 (PUR-R23, PMT-R02):

```http
POST /payment-sessions
Authorization: Bearer <PAYMENT_API_TOKEN>
Content-Type: application/json

{"booking_reference": "BK-7KQ2M9", "amount_satang": 45000, "currency": "THB",
 "description": "Meeting Room A, 2026-10-07 09:00-10:30",
 "success_url": "http://localhost:8001/bookings/BK-7KQ2M9/return",
 "cancel_url": "http://localhost:8001/bookings/BK-7KQ2M9",
 "expires_at": "2026-10-05T10:13:00+07:00"}
```

```http
HTTP/1.1 201 Created

{"id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A", "url": "http://localhost:8002/pay/ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "status": "open", "payment_status": "unpaid", "amount_satang": 45000, "currency": "THB",
 "booking_reference": "BK-7KQ2M9", "expires_at": "2026-10-05T10:13:00+07:00"}
```

Member A pays with 4242424242424242, 12/28, 123 at 10:04. Payment answers `303 Location: http://localhost:8001/bookings/BK-7KQ2M9/return?session_id=ps_Q7mZ3xK9vT2bN8rL4wYc1A`. Purchase then reads its stored session id (never the query value, PUR-R24):

```http
GET /payment-sessions/ps_Q7mZ3xK9vT2bN8rL4wYc1A
```

```http
HTTP/1.1 200 OK

{"id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A", "url": "http://localhost:8002/pay/ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "status": "complete", "payment_status": "paid", "amount_satang": 45000, "currency": "THB",
 "booking_reference": "BK-7KQ2M9", "expires_at": "2026-10-05T10:13:00+07:00"}
```

Reference, amount and currency match, so Purchase confirms BK-7KQ2M9 and requests the grant (PUR-R25, PUR-R26).

Refund after Member A cancels at 2026-10-05 11:00, 46 h before start, 100% (PUR-R30, PUR-R32):

```http
POST /refunds

{"payment_session_id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A", "booking_reference": "BK-7KQ2M9",
 "amount_satang": 45000, "reason": "member_cancel", "attempt": 1}
```

```http
HTTP/1.1 201 Created

{"id": "re_3Jd8Qk2Vn7Xp5Lw9Tz4Hc6", "payment_session_id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "booking_reference": "BK-7KQ2M9", "amount_satang": 45000, "reason": "member_cancel",
 "attempt": 1, "status": "succeeded"}
```

### 5.2 Invalid input

Amount below THB 10.00 (PMT-R02):

```http
POST /payment-sessions

{"booking_reference": "BK-9MZ4RC", "amount_satang": 999, "currency": "THB",
 "description": "Focus Pod 1, 2026-10-05 13:00-13:30",
 "success_url": "http://localhost:8001/bookings/BK-9MZ4RC/return",
 "cancel_url": "http://localhost:8001/bookings/BK-9MZ4RC",
 "expires_at": "2026-10-05T10:13:00+07:00"}
```

```http
HTTP/1.1 400 Bad Request

{"error": {"code": "invalid_request", "message": "amount_satang must be at least 1000 (THB 10.00)"}}
```

Other 400 messages: `"expires_at must be ISO 8601 with an offset"` (no offset), `"expires_at must be in the future"` (new session, `expires_at` not after now), `"amount_satang is out of range"` (9223372036854775808). Refund `attempt` 0 or `amount_satang` 0 gets 400 too (PMT-R14).

No token at all (PMT-R01). The token is checked before the body:

```http
POST /payment-sessions
Content-Type: application/json

{"booking_reference": "BK-7KQ2M9", "amount_satang": 5}
```

```http
HTTP/1.1 401 Unauthorized

{"error": {"code": "unauthorized", "message": "unauthorized"}}
```

### 5.3 Failure

A decline is not a final outcome. Member A pays with 4000000000000002 at 10:02. Payment answers `303 Location: /pay/ps_Q7mZ3xK9vT2bN8rL4wYc1A`; the GET then shows `<p data-decline-code="generic_decline">Your card was declined.</p>` once, carried by the `payment_session` flash (PMT-R10). Purchase then reads:

```http
GET /payment-sessions/ps_Q7mZ3xK9vT2bN8rL4wYc1A
```

```http
HTTP/1.1 200 OK

{"id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A", "url": "http://localhost:8002/pay/ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "status": "open", "payment_status": "unpaid", "amount_satang": 45000, "currency": "THB",
 "booking_reference": "BK-7KQ2M9", "expires_at": "2026-10-05T10:13:00+07:00"}
```

The booking stays held. Member A retries on the same page.

Nobody pays. At 10:14 the session reads expired, so no late payment can arrive (PMT-R05, PMT-R11). At 10:15 Purchase expires the booking (PUR-R24):

```http
HTTP/1.1 200 OK

{"id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A", "url": "http://localhost:8002/pay/ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "status": "expired", "payment_status": "unpaid", "amount_satang": 45000, "currency": "THB",
 "booking_reference": "BK-7KQ2M9", "expires_at": "2026-10-05T10:13:00+07:00"}
```

The first refund fails. Member C paid BK-3HT8WD (THB 1,000.00) with 4000000000005126 (PMT-R16):

```http
POST /refunds

{"payment_session_id": "ps_R2kW8nT5vQ9mX3bL7yHd4E", "booking_reference": "BK-3HT8WD",
 "amount_satang": 100000, "reason": "member_cancel", "attempt": 1}
```

```http
HTTP/1.1 201 Created

{"id": "re_7Kp2Xw9Qm4Tn8Rv3Lc6Yb5", "payment_session_id": "ps_R2kW8nT5vQ9mX3bL7yHd4E",
 "booking_reference": "BK-3HT8WD", "amount_satang": 100000, "reason": "member_cancel",
 "attempt": 1, "status": "failed"}
```

Purchase stores `failed` and retries nothing. Only the Operator's Retry sends attempt 2, which succeeds (PUR-R33). The operator page shows "needs manual follow-up" until then (PMT-R17).

Over the balance. BK-7KQ2M9 collected 45000 and already refunded 45000 (PMT-R15):

```http
POST /refunds

{"payment_session_id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A", "booking_reference": "BK-7KQ2M9",
 "amount_satang": 1, "reason": "operator_cancel", "attempt": 2}
```

```http
HTTP/1.1 409 Conflict

{"error": {"code": "refund_exceeds_collected", "message": "refund exceeds amount collected"}}
```

A held cancel while the Member already paid in another tab (PUR-R31 row 5, PMT-R06):

```http
POST /payment-sessions/ps_Q7mZ3xK9vT2bN8rL4wYc1A/expire
```

```http
HTTP/1.1 200 OK

{"id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A", "url": "http://localhost:8002/pay/ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "status": "complete", "payment_status": "paid", "amount_satang": 45000, "currency": "THB",
 "booking_reference": "BK-7KQ2M9", "expires_at": "2026-10-05T10:13:00+07:00"}
```

Purchase confirms without a grant, then cancels as a confirmed booking under the refund policy.

### 5.4 Repeat

Member A presses "Continue to payment" again at 10:05. Purchase sends the same body. Payment returns the stored session (PMT-R03):

```http
HTTP/1.1 200 OK

{"id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A", "url": "http://localhost:8002/pay/ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "status": "open", "payment_status": "unpaid", "amount_satang": 45000, "currency": "THB",
 "booking_reference": "BK-7KQ2M9", "expires_at": "2026-10-05T10:13:00+07:00"}
```

The same reference with another amount changes nothing:

```http
POST /payment-sessions

{"booking_reference": "BK-7KQ2M9", "amount_satang": 30000, "currency": "THB",
 "description": "Meeting Room A, 2026-10-07 09:00-10:30",
 "success_url": "http://localhost:8001/bookings/BK-7KQ2M9/return",
 "cancel_url": "http://localhost:8001/bookings/BK-7KQ2M9",
 "expires_at": "2026-10-05T10:13:00+07:00"}
```

```http
HTTP/1.1 409 Conflict

{"error": {"code": "session_conflict", "message": "BK-7KQ2M9 already has a payment session for 45000 THB"}}
```

Refund repeat after a timeout. Purchase resends attempt 1 unchanged and gets the stored result, with the same `re_` id and no second refund (PMT-R14):

```http
HTTP/1.1 200 OK

{"id": "re_3Jd8Qk2Vn7Xp5Lw9Tz4Hc6", "payment_session_id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "booking_reference": "BK-7KQ2M9", "amount_satang": 45000, "reason": "member_cancel",
 "attempt": 1, "status": "succeeded"}
```

A repeat of a failed attempt returns `"status": "failed"` again. Repeating never retries.

Double-click on Pay: the second POST waits on the row lock, sees `complete`, stores nothing and answers 303 to `success_url`. The amount collected is 45000, not 90000 (PMT-R12).

### 5.5 Coverage skips collection

**No call is made to Payment.** Member B (plan_active true) books Meeting Room A 2026-10-07 10:30-11:30 (BK-3MZ8QT, THB 300.00), and Member A books Community Table (THB 0 per hour). Both bookings are confirmed at once, with coverage `plan` or `free` (PUR-R19, PUR-R20). Purchase sends no POST /payment-sessions, no GET, no expire on cancel and no POST /refunds. The cancel refund is 0 and the screen says "No payment was taken" (PUR-R30). Payment has no record of these bookings, and its operator totals do not include them (PMT-R04, PMT-R17).

For the same reason Payment refuses `amount_satang` 0 with 400 (5.2). A card form for THB 0.00 cannot exist.

## 6. How Purchase applies each answer

| Purchase call | Answer | Purchase does | Rule |
|---|---|---|---|
| Create session | 201 or 200 | Store `id`, send the browser 303 to `url` | PUR-R23 |
| Create session | No answer or 5xx | Booking stays held without a session. JSON 503, form flash "Payment is not reachable. Please try again." | PUR-R23, PUR-Q09 |
| Read or expire | paid, reference, amount and currency equal the booking's | Confirm, then request the grant (or, after expire, cancel under the policy) | PUR-R25, PUR-R31 |
| Read | paid, mismatch | One transaction: cancelled, `amount_mismatch`, full refund recorded. Send it after commit with the session's own reference | PUR-R25 |
| Read | paid, booking already expired | Full refund, reason `slot_unavailable` | PUR-R25 |
| Read | unpaid, hold lapsed | Mark the booking expired | PUR-R24 |
| Read | unpaid, inside the hold | No change | PUR-R24 |
| Expire | unpaid, inside the hold | Cancelled, no refund | PUR-R31 |
| Expire | unpaid, hold lapsed | Expired, flash "This hold has already expired" | PUR-R31 |
| Any read | No answer or 5xx | No change. A read page shows "Payment status unknown, refresh later". A request that needs the result (conflicting insert, cancel) gets 503 or a flashed "try again" | PUR-R22, PUR-R24, PUR-R31 |
| Refund | succeeded | Store `refund_status` succeeded and the attempt | PUR-R32 |
| Refund | failed | Store failed. Page says "Refund failed. The operator will follow up." Only the Operator starts attempt+1 | PUR-R33 |
| Refund | No answer or 5xx | `refund_status` pending. Resend the same attempt on the next booking page open or Retry | PUR-R32 |

## 7. Timeouts and retries

- Every call uses `timeout=5` seconds (D28, PUR-R35).
- A timeout, a connection error or a 5xx answer means "unreachable". Purchase stores no result for it.
- Purchase never calls Payment while a database transaction is open. It commits first, calls, then stores the answer in a new short transaction (PUR-R35, D13).
- Purchase makes one attempt per call inside a request. It does not loop or sleep. The next touch retries:
  - the reconcile read (GET) at the reconcile points of PUR-R24: the booking page and its return URL, GET /api/bookings/<ref>, My bookings (the page and GET /api/bookings/mine), the cancel confirm screen, the pre-insert sweep, the Member's own lapsed holds (PUR-R39), and the Operator's Reconcile of one booking or of all held; the POST cancel uses expire in place of the GET (PUR-R31);
  - a pending refund: the booking page, the owner's Retry and the Operator's Retry (PUR-R32).
- Retries are safe because every write has a natural key: `booking_reference` for a session, the session id for expire, `(payment_session_id, attempt)` for a refund (ADR-0014). A retry resends the same body.
- Any other 4xx (400, 401, 404, 409) is a defect in Purchase or its config. Purchase logs the operation, the booking reference and the status (never the token), stores nothing and handles the step like "unreachable": pending, or "try again" where the request needs the answer (PUR-R35).
- Payment never retries anything and never calls back (PMT-R18, ADR-0004).

## 8. Test-observable page markers

| Page | Marker | Values |
|---|---|---|
| GET `/pay/<id>` after a decline (the 303 target) | element with `data-decline-code="<code>"` holding the reason text | `generic_decline`, `insufficient_funds`, `expired_card`, `processing_error` (PMT-T09) |
| POST `/pay/<id>` at or after `expires_at` (409) | page text "This payment session has expired. Nothing was charged." and a "Back to your booking" link | |
| GET `/operator`, session rows | `data-session-id="ps_..."`, `data-booking-reference="BK-7KQ2M9"` | |
| GET `/operator`, attempt rows | `data-session-id`, `data-booking-reference`, `data-attempt-result`, and on a declined row `data-decline-code` | `succeeded`, `declined`; the codes of PMT-T09 |
| GET `/operator`, refund rows | `data-session-id`, `data-booking-reference`, `data-refund-attempt="1"`, `data-refund-status` | `succeeded`, `failed`. A test scopes rows by `data-booking-reference`, because the shared stack keeps earlier runs' rows |
| GET `/operator`, follow-up | element `data-follow-up="BK-3HT8WD"` with the text "needs manual follow-up", present only while that failed refund has no later succeeded attempt | the booking reference |
| GET `/operator`, totals | `data-collected-satang`, `data-refunded-satang`, `data-net-satang`, `data-commission-satang` | integers; the e2e suite asserts before-and-after deltas, because the shared stack is never reset |

## 9. Versioning

- This draft is state proposed. At M4 a consumer-lens reviewer signs it off in `REVIEW_LOG.md`; then `cowork-booking-payment` is tagged `contract-v1`, and this text moves to `cowork-booking-payment/CONTRACT.md` with `openapi.yaml` (ADR-0005).
- Any change after `contract-v1`, even an added response field, goes through a CONTRACT_CHANGE_REQUEST and the tag `contract-v2`.
- The paths carry no version prefix. Consumers ignore response fields they do not know.
