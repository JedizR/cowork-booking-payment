"""The only source of time (D27, PMT-R20)."""

import os
from datetime import datetime, timedelta, timezone

from flask import current_app

LOCAL_TZ = timezone(timedelta(hours=7))  # Bangkok, no daylight saving (D2)


def test_clock_enabled() -> bool:
    return os.getenv("TEST_CLOCK_ENABLED") == "true"


def now() -> datetime:
    """Real time, or the stored override while TEST_CLOCK_ENABLED is exactly "true".

    Reads the one-row test_clock table on every call (no cache), so both
    gunicorn workers see a new instant from the next request.
    """
    if test_clock_enabled():
        row = current_app.db.execute(
            "SELECT now_override FROM test_clock WHERE id = 1"
        ).fetchone()
        if row and row["now_override"] is not None:
            return row["now_override"].astimezone(LOCAL_TZ)
    return datetime.now(LOCAL_TZ)
