# Provenance

This repository was seeded from `cs403bkk-2026/spacey` at
`5a1cf3d90e538f431625cbb959987b7bdbe3c946` by copy-and-prune (ADR-0006): the seed commit copies
the class repository's tree, and the next commit, `refactor: prune to payment context`, removes
everything outside the Payment context.

## Removed before the seed commit

`STARTUP_LOG.md`, `LOAD_TEST.md`, `CONTRIBUTING.md`, `scripts/`, `deploy/` (the Nomad job) and
`.github/workflows/delivery.yml` (it pushed to ghcr and deployed on every push to main). Persona
names in the copied text were replaced by Member A, Member B and Member C.

## Kept by the prune

- `validate_card` and the card regexes `CARD_NUMBER_RE`, `CVC_RE`, `EXPIRY_RE` (app.py); M5 maps
  its codes to the PMT-R08 texts and reads the month from `clock.now()`.
- The payment form part of `templates/confirmation.html`; it becomes the hosted page `/pay/<id>`.
- What every service keeps: `/health` with `revision`, the fail-fast database connect, the
  gunicorn query-string scrubbing access log, `templates/base.html` + `static/style.css`, and the
  `money` / `local_time` filters (`money` now shows "THB 1,234.50").

## Deleted by the prune

- Spaces, bookings, subscriptions, users / members, register / login / logout and
  `inject_current_user`, metrics and the dashboard, unlock and `issue_access_code`, the old
  `/bookings/<id>/pay` route with its `force_failure` hook, `purchase.py`, the startup backfill
  and the seed's tables.
- Templates: `index.html`, `login.html`, `register.html`, `my_bookings.html`, `dashboard.html`,
  `booking_not_found.html`, and the booking and unlock parts of `confirmation.html`.
- Tests: `tests/test_app.py`, `tests/test_purchase.py` (they cover deleted code). New tests are
  named after the PMT rule they prove.

## Rebranding

The product name, page titles, compose project, container, image and database names are
"Cowork Booking" / `cowork-booking-payment`. The seed's name appears only in this file.

## Seed flaws relevant to Payment

From `cowork-booking-docs/DECISIONS.md`, "Seed flaws and where they are handled".

| Flaw | Summary | Handled by | Fixed or out of scope |
|---|---|---|---|
| F3 | Unpaid holds block the slot forever | D12; PMT-R11 | Fixed: the session expires 2 minutes before the hold; no attempt at or after `expires_at` |
| F5 | Cancel hard-deletes the booking and erases revenue | D18, D19; PMT-R16, PMT-R17 | Fixed: the paid session and its refunds stay recorded; nothing is deleted |
| F8 | Public dashboard | D24; PMT-R17 | Fixed: money totals only on the Operator page behind HTTP Basic |
| F9 | INTEGER amount overflow gives a 500 | D1; PMT-R02, PMT-R14 | Fixed: BIGINT satang; an integer outside its column range gets 400 |
| F10 | Default SECRET_KEY used in deploy | D15; PMT-R19 | Fixed: SECRET_KEY required, fail fast |
| F12 | `?error=` text is reflected into pages | D28; PMT-R10 | Fixed: Flask `flash()` only |
| F13 | No CSRF tokens on POST forms | D15; PMT-R19 | Fixed by mitigation: SameSite=Lax cookie (ADR-0009) |
| F14 | The pay check-then-update is not atomic | D28; PMT-R06, PMT-R12 | Fixed: attempt and status update in one transaction with `SELECT … FOR UPDATE` |
| F16 | A 0-amount booking still asks for a card | D10, D1; PMT-R02, PMT-R08 | Fixed: Payment refuses amounts below THB 10 |
| A1 | Pay racing cancel can crash with a 500 | D18, D28; PMT-R06, PMT-R12 | Fixed: expire and pay lock the same session row |
| A6 | Test hook `force_failure` is live in production | D27; PMT-R01, PMT-R09, PMT-R20 | Fixed: the hook is removed; outcomes come from the test cards; the only test hook is the clock, 404 unless `TEST_CLOCK_ENABLED` |
| A7 | Naive time: the form accepts it, JSON rejects it | D2; PMT-R02 | Fixed: JSON needs an offset; a naive time gets 400 |
| A8 | One shared connection, no reconnect, DDL at import | D28; PMT-R12 | Fixed in part: one connection per worker, 2 workers, explicit transactions. Out of scope: reconnect; the `ponytail:` comment in `gunicorn.conf.py` names the psycopg_pool upgrade path |
| A10 | Money is shown in dollars | D1; PMT-R07 | Fixed: "THB 1,234.50", satang integers |
| A11 | No injectable clock | D27; PMT-R11, PMT-R20 | Fixed: `clock.now()` everywhere; the override works only behind `TEST_CLOCK_ENABLED` |

The other rows (F1, F2, F4, F6, F7, F11, F15, A2-A5, A9, A12-A15) belong to Purchase or Access.
