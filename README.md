# Cowork Booking — Payment

The Payment service of Cowork Booking: a mocked, Stripe-Checkout-like payment step. No real money
moves; test cards decide every outcome.

**Owns:** payment sessions (one per booking reference, amount fixed as sent), payment attempts
(card brand and last4 only), refunds (synchronous `succeeded` / `failed`), and the money totals
on the Operator page. It never prices a booking, never sees coverage, and calls no other service
(PMT-R04, PMT-R18).

- Contract: [CONTRACT.md](CONTRACT.md) and [openapi.yaml](openapi.yaml) (frozen at tag `contract-v1`)
- Rules, glossary, decisions: [cowork-booking-docs](https://github.com/JedizR/cowork-booking-docs)
  (`RULES.md` PMT-R01..PMT-R20, `GLOSSARY.md`, `DECISIONS.md`)
- Seed and prune record: [PROVENANCE.md](PROVENANCE.md)

## Quick start

Requires Docker.

```sh
docker compose up -d --build --wait
curl http://localhost:8002/health        # {"revision":"compose","status":"ok"}
docker compose down
```

While it runs:

- Create a session (the call Purchase makes):

  ```sh
  curl -X POST http://localhost:8002/payment-sessions \
    -H "Authorization: Bearer dev-payment-api-token-0123456789abcdef" \
    -H "Content-Type: application/json" \
    -d '{"booking_reference":"BK-7KQ2M9","amount_satang":45000,"currency":"THB",
         "description":"Meeting Room A, 2026-10-07 09:00-10:30",
         "success_url":"http://localhost:8001/bookings/BK-7KQ2M9/return",
         "cancel_url":"http://localhost:8001/bookings/BK-7KQ2M9",
         "expires_at":"<an ISO time with offset, a few minutes from now>"}'
  ```

- Open the `url` from the answer (`http://localhost:8002/pay/ps_…`) and pay with a test card.
- Operator page: <http://localhost:8002/operator>, user `operator`, password
  `dev-operator-password` (the compose default).

The compose defaults are for local use only. Set real values in `.env` (see `.env.example`).

## Routes

| Route | Who | What |
|---|---|---|
| `POST /payment-sessions` | Purchase (bearer) | Create, or return the existing session for the booking reference |
| `GET /payment-sessions/<id>` | Purchase (bearer) | Read a session (`open`, `complete`, `expired`) |
| `POST /payment-sessions/<id>/expire` | Purchase (bearer) | Expire an open session; a paid one stays paid |
| `POST /refunds` | Purchase (bearer) | Refund; `(payment_session_id, attempt)` is the idempotency key |
| `GET`/`POST /pay/<id>` | Member's browser | Hosted checkout page; the session id is the bearer link |
| `GET /operator` | Operator (HTTP Basic) | Sessions, attempts, refunds, totals, failed refunds |
| `GET /health` | anyone | `{"status": "ok", "revision": APP_REVISION}` |
| `POST /_test/clock` | e2e only | 404 unless `TEST_CLOCK_ENABLED=true` |

## Test cards

Any future expiry, any CVC. Every other valid-looking number declines with `generic_decline`.

| Card | Result |
|---|---|
| 4242424242424242 | success |
| 4000000000000002 | generic_decline |
| 4000000000009995 | insufficient_funds |
| 4000000000000069 | expired_card |
| 4000000000000119 | processing_error |
| 4000000000005126 | success; the first refund of that session fails, later ones succeed |

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `DATABASE_URL` | yes | Postgres 16. The schema is created at start; an unreachable database stops the start |
| `SECRET_KEY` | yes | At least 32 characters, not the seed default. Signs the `payment_session` flash cookie |
| `PAYMENT_API_TOKEN` | yes | At least 32 characters. Purchase sends it as `Authorization: Bearer …` |
| `OPERATOR_PASSWORD` | yes | At least 12 characters. HTTP Basic password for `/operator` |
| `PUBLIC_URL` | no | Base of the session `url` (default `http://localhost:8002`). `https` adds `Secure` to the cookie |
| `APP_REVISION` | no | Shown by `/health` (default `local`) |
| `TEST_CLOCK_ENABLED` | e2e only | Exactly `true` enables `POST /_test/clock`. Never set in the Dockerfile, `compose.yaml` or `.env.example` |

## Ports

| Port | What |
|---|---|
| 8002 | Payment (container port 8000) |
| 5442 | Payment's Postgres, published on 127.0.0.1 by this repo's `compose.yaml` only |

## Tests

Tests run against a real Postgres 16 and rebuild the schema per test. Each test is named after the
rule it proves (`test_pmt_r12_…`).

```sh
docker run -d --rm --name payment-test-pg -e POSTGRES_PASSWORD=postgres -p 55462:5432 postgres:16
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
DATABASE_URL=postgresql://postgres:postgres@localhost:55462/postgres \
  .venv/bin/python -m pytest -q --junitxml=reports/junit.xml
docker stop payment-test-pg
```

CI (`.github/workflows/ci.yml`) runs the same suite against `postgres:16`, then `docker build`.

## Layout

`app.py` (routes, transactions), `payment.py` (field and card checks, test cards, money, schema),
`clock.py` (the only source of time), `templates/` + `static/style.css` (the shared look),
`gunicorn.conf.py` (2 workers, one DB connection each, query-string-free access log).
