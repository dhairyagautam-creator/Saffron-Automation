"""Generic union-merge cloud sync for small, directly-managed recipient
lists (MasterEmailRecipient, InventoryEmailRecipient) -- reuses
app/parameter_sync_service.py's push_config()/pull_config() against the
SAME module_configurations table, storing a recipient list as
`{"recipients": [...]}` rather than scalar key/value pairs. Each
recipient table gets its own module_key ("master_email_recipients",
"inventory_email_recipients") so a change to one never touches the
other's row.

DEDUP KEY: email address alone (case-insensitive), not name+email+division.
MasterEmailRecipient's `division` is a single value, but
InventoryEmailRecipient's `divisions` is a comma-separated list -- using
either as part of a composite key means two semantically-identical
recipients ("b@x.com" with "ONYX,XANDRA" vs "XANDRA,ONYX") could compare
as different rows purely from list ordering, which no simple dict/string
comparison resolves cleanly. A real person is identified by their email
address regardless of which divisions they're currently configured for;
two rows sharing an email are always the same intended recipient, never
two legitimately different ones.

UNION-MERGE ONLY, NEVER DELETE: a remote recipient (by email) not already
present locally is added; a local recipient not present remotely is
NEVER removed by a pull. This is a pure additive union, not a
last-writer-wins field merge -- if the same email exists on two machines
with different name/division data, the LOCAL row wins untouched (the
remote copy is simply treated as "already have this one", not applied).
True two-way delete propagation (removing a recipient everywhere once
removed on one machine) is explicitly NOT built here -- flagged as a
future consideration, not silently attempted.
"""

from app.parameter_sync_service import check_for_config_update, push_config


def _email_key(recipient: dict) -> str:
    return (recipient.get("email") or "").strip().lower()


def push_recipients(module_key: str, recipients: list[dict]) -> tuple[bool, str | None]:
    """Pushes the FULL current local recipient list for `module_key` --
    called after every local add/edit/delete (see each recipient
    service's own push-after-write wiring), same "auto-push on save"
    contract as scalar parameters."""
    return push_config(module_key, {"recipients": recipients})


def check_for_recipient_update(module_key: str, local_recipients: list[dict]) -> dict:
    """Pulls the remote recipient list for `module_key` and compares it
    against `local_recipients` by email. Returns pull_config()'s own
    dict shape (see app.parameter_sync_service.pull_config) plus:
      - `changed`: True iff at least one remote recipient's email isn't
        present locally yet (never True just because local has rows
        remote doesn't -- those get included next time THIS machine
        pushes, they don't make an incoming pull "changed").
      - `new_recipients`: the remote recipients to actually add on
        apply -- already filtered down to the ones missing locally.
    `reason: 'not_found'` (nobody has ever pushed this module_key) is
    reported as ok with changed=False, same "no banner" treatment as
    pull_config's own contract for scalar parameters.
    """
    result = check_for_config_update(module_key, {"recipients": local_recipients})
    if not result["ok"]:
        if result.get("reason") == "not_found":
            return {"ok": True, "changed": False, "new_recipients": []}
        return result

    remote_recipients = result["config"].get("recipients", [])
    local_emails = {_email_key(r) for r in local_recipients}
    new_recipients = [r for r in remote_recipients if _email_key(r) not in local_emails]

    result["new_recipients"] = new_recipients
    result["changed"] = bool(new_recipients)
    return result
