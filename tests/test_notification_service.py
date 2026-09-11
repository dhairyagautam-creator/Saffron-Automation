"""Pure-logic checks for app/notification_service.py's send_button_state()
-- the Path Validator "Send Emails" button's no_data/new_data/resend_confirm
classification. No DB/network -- takes already-fetched values.
"""

from app.notification_service import send_button_state


def test_no_active_import_is_no_data():
    assert send_button_state(has_active_import=False, already_sent_for_import=False) == "no_data"


def test_active_import_never_sent_is_new_data():
    assert send_button_state(has_active_import=True, already_sent_for_import=False) == "new_data"


def test_active_import_already_sent_is_resend_confirm():
    assert send_button_state(has_active_import=True, already_sent_for_import=True) == "resend_confirm"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Notification service pure-logic checks: all passed")
