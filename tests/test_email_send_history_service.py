"""Pure-logic checks for app/email_send_history_service.py's
format_relative_time() -- no DB/network, takes a plain datetime."""

from datetime import timedelta

from app.email_send_history_service import format_relative_time
from database.connection import utcnow


def test_just_now():
    assert format_relative_time(utcnow() - timedelta(seconds=30)) == "just now"


def test_minutes_ago():
    assert format_relative_time(utcnow() - timedelta(minutes=5)) == "5m ago"


def test_hours_ago():
    assert format_relative_time(utcnow() - timedelta(hours=3)) == "3h ago"


def test_days_ago():
    assert format_relative_time(utcnow() - timedelta(days=2)) == "2d ago"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Email send history service pure-logic checks: all passed")
