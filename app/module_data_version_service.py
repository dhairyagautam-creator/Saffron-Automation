"""Phase 2 of email authority's local data_version counter -- a stand-in
for a real sync manifest seq, for the two modules that don't have one yet
(Inventory, Work Distribution). See docs/EMAIL_AUTHORITY_PHASE2_CONTEXT.md
for why this exists and why it's intentionally NOT the same thing as a
manifest seq.

Not used by Path Validator -- it already has an exact identifier
(active_session.import_id, see app/session_state.py).
"""

from database.connection import get_session, utcnow
from database.models import ModuleDataVersion


def bump_data_version(module: str) -> int:
    """Increment `module`'s counter by 1 (creating it at 1 if this is the
    first time) and return the new value. Call exactly once per actual
    data change -- see the Phase 2 report for the confirmed hook point per
    module (the data table's own full-replace, not an upload-log entry)."""
    session = get_session()
    try:
        row = session.query(ModuleDataVersion).filter_by(module=module).first()
        if row is None:
            row = ModuleDataVersion(module=module, version=1, updated_at=utcnow())
            session.add(row)
        else:
            row.version += 1
            row.updated_at = utcnow()
        session.commit()
        return row.version
    finally:
        session.close()


def get_data_version(module: str) -> int:
    """Current counter value for `module`, or 0 if it has never been
    bumped (no data yet)."""
    session = get_session()
    try:
        row = session.query(ModuleDataVersion).filter_by(module=module).first()
        return row.version if row else 0
    finally:
        session.close()
