"""Pure-logic checks for app/inventory_notification_service.py's
send_button_state() -- the Automated Emails page's no_data/new_data/
resend_confirm classification. No DB/network -- takes already-fetched values.
"""

from app.inventory_notification_service import send_button_state


def test_no_data_at_all():
    assert send_button_state(has_data=False, changed_since_last_send=False) == "no_data"


def test_data_present_never_sent_is_new_data():
    assert send_button_state(has_data=True, changed_since_last_send=True) == "new_data"


def test_data_present_already_sent_is_resend_confirm():
    assert send_button_state(has_data=True, changed_since_last_send=False) == "resend_confirm"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Inventory notification service pure-logic checks: all passed")
