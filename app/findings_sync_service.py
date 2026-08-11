"""Cloud sync for investigation_findings -- Path Validator, Version 2.0
Milestone 15; Phase 1 sync-reliability rebuild.

Reliability model (Phase 1, findings only):

  * Every local finding carries a persistent `dirty` flag and a
    `cloud_version` token (see database/models.py). `dirty=1` means a local
    business change (suppression, reviewer status) has not yet been
    acknowledged by the cloud; `cloud_version` is the cloud row's own
    `updated_at` value this local row last acknowledged -- a SERVER-origin
    token, never a local datetime.now().

  * reconcile runs in version/dirty mode (see
    app/sync_service.reconcile_rows()): a dirty local finding is NEVER pulled
    over by a cloud snapshot -- it is pushed instead. A clean local finding
    adopts the cloud copy only when the cloud's version token differs from the
    one it last acknowledged. No wall-clock comparison happens anywhere, so
    clock skew cannot reverse a decision (the old "slingshot" bug).

  * Pushing a dirty finding uses optimistic concurrency (CAS): update the
    cloud row only if its `updated_at` still equals our acknowledged
    `cloud_version`. If it does -> store the server's new version and clear
    dirty. If it doesn't -> the cloud changed under us: log the CONFLICT and
    keep the local change (dirty stays set); the cloud is NOT overwritten and
    the local copy is NOT reverted. Brand-new findings (no acknowledged
    version yet) are created with a plain upsert.

This module is deliberately scoped to Path Validator findings for this phase;
other modules still use the legacy timestamp reconcile unchanged.
"""

import uuid
from datetime import date, datetime

from loguru import logger

from app.mode_state import is_developer_mode
from app.sync_service import push_rows, reconcile_rows, update_row_cas
from database.connection import get_session
from database.models import ImportHistory, InvestigationFinding

TABLE = "path_validator_findings"

_COLUMNS = (
    "employee_name",
    "employee_code",
    "visit_date",
    "rule_name",
    "message",
    "division",
    "concentration_percent",
    "valid_visit_count",
    "matched_visit_count",
    "radius_meters",
    "threshold_percent",
    "cluster_lat",
    "cluster_lon",
    "notification_status",
    "suppression_reason",
    "hospital_name",
    "hospital_lat",
    "hospital_lon",
    "hospital_distance_meters",
    "status",
)


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _parse_date(raw):
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()


def _serialize(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _get_import_cloud_id(import_id: int) -> str | None:
    session = get_session()
    try:
        history = session.query(ImportHistory).filter_by(id=import_id).first()
        return history.cloud_id if history else None
    finally:
        session.close()


def _cloud_payload(finding, import_cloud_id: str) -> dict:
    """The whole-row cloud payload for one finding (cloud_id + import link +
    the mirrored data columns). `updated_at` is deliberately omitted -- the
    Supabase trigger assigns it, and it is what CAS conditions on."""
    row = {"cloud_id": finding.cloud_id, "import_cloud_id": import_cloud_id}
    for column in _COLUMNS:
        row[column] = _serialize(getattr(finding, column))
    return row


def classify_cas_result(result) -> tuple[str, object]:
    """Map an app.sync_service.update_row_cas() result to an outcome:

      ('acknowledged', new_version) -- CAS applied; store new_version + clear dirty
      ('conflict', None)            -- cloud moved since we acknowledged; keep the
                                       local change (dirty stays), don't overwrite
      ('error', None)               -- network/other failure; keep dirty, retry later

    Pure/side-effect-free so the outcome policy is unit-testable without a
    network or database."""
    if not result.success:
        return ("error", None)
    if not result.rows:
        return ("conflict", None)
    return ("acknowledged", result.rows[0].get("updated_at"))


def sync_findings_for_import(import_id: int) -> bool:
    """Reconcile this import's findings with the cloud under the version/dirty
    rule. A dirty local finding can never be overwritten by a cloud pull.
    Returns True if any local row changed (i.e. something was pulled). No-op
    (False) in Developer Mode."""
    if is_developer_mode():
        return False

    import_cloud_id = _get_import_cloud_id(import_id)
    if import_cloud_id is None:
        logger.warning(f"Sync: cannot sync findings for import_id={import_id} -- import has no cloud_id yet")
        return False

    # 1. Local snapshot. Each row carries its acknowledged cloud version under
    #    "updated_at" (what reconcile compares against the cloud's current
    #    version) plus its dirty flag; "_expected_version" drives the push.
    session = get_session()
    try:
        findings = session.query(InvestigationFinding).filter_by(import_id=import_id).all()
        local_rows = []
        for finding in findings:
            if not finding.cloud_id:
                finding.cloud_id = str(uuid.uuid4())
            row = {"cloud_id": finding.cloud_id, "import_cloud_id": import_cloud_id}
            for column in _COLUMNS:
                row[column] = _serialize(getattr(finding, column))
            row["updated_at"] = finding.cloud_version
            row["_dirty"] = bool(finding.dirty)
            row["_expected_version"] = finding.cloud_version
            local_rows.append(row)
        session.commit()
    finally:
        session.close()

    plan = reconcile_rows(
        TABLE,
        local_rows,
        key_columns=["cloud_id"],
        filters={"import_cloud_id": import_cloud_id},
        dirty_column="_dirty",
        version_column="updated_at",
    )
    if not plan.success:
        logger.warning(f"Sync: failed to reconcile findings for import_id={import_id}: {plan.error_message}")
        return False

    changed = False
    ack: dict[str, object] = {}  # cloud_id -> new server version to store, dirty cleared
    conflicts = 0

    # 2. Push. New findings (no acknowledged version) go in one upsert; existing
    #    dirty findings use per-row optimistic CAS so a concurrent cloud edit is
    #    detected rather than blindly overwritten.
    new_rows = [r for r in plan.to_push if not r.get("_expected_version")]
    dirty_rows = [r for r in plan.to_push if r.get("_expected_version")]

    if new_rows:
        payloads = [{k: v for k, v in r.items() if not k.startswith("_") and k != "updated_at"} for r in new_rows]
        result = push_rows(TABLE, payloads, on_conflict="cloud_id")
        if not result.success:
            logger.warning(f"Sync: failed to push {len(payloads)} new finding(s): {result.error_message}")
        else:
            for pushed in result.rows:
                ack[pushed.get("cloud_id")] = pushed.get("updated_at")
            logger.info(f"Sync: pushed {len(payloads)} new finding(s) for import_id={import_id}")

    for r in dirty_rows:
        cloud_id = r["cloud_id"]
        expected = r["_expected_version"]
        body = {k: v for k, v in r.items() if not k.startswith("_") and k not in ("updated_at", "cloud_id")}
        cas = update_row_cas(
            TABLE,
            key_column="cloud_id",
            key_value=cloud_id,
            version_column="updated_at",
            expected_version=expected,
            row=body,
        )
        outcome, new_version = classify_cas_result(cas)
        if outcome == "acknowledged":
            ack[cloud_id] = new_version
        elif outcome == "conflict":
            conflicts += 1
            logger.warning(
                f"Sync CONFLICT: finding cloud_id={cloud_id!r} changed in the cloud since version "
                f"{expected!r}; preserving the local change (kept dirty), cloud NOT overwritten, local NOT reverted."
            )
        else:  # error
            logger.warning(f"Sync: CAS push error for finding cloud_id={cloud_id!r}: {cas.error_message}")

    # 3. Persist acknowledgements: store the new server version, clear dirty.
    if ack:
        now = datetime.now()
        session = get_session()
        try:
            for cloud_id, new_version in ack.items():
                finding = session.query(InvestigationFinding).filter_by(cloud_id=cloud_id).first()
                if finding is not None:
                    finding.cloud_version = new_version
                    finding.dirty = 0
                    finding.synced_at = now
            session.commit()
        finally:
            session.close()

    # 4. Apply pulls. reconcile only ever selects CLEAN local rows (or
    #    cloud-only rows) for pull, so this can never clobber a dirty change.
    if plan.to_pull:
        now = datetime.now()
        session = get_session()
        try:
            for cloud_row in plan.to_pull:
                cloud_id = cloud_row["cloud_id"]
                finding = session.query(InvestigationFinding).filter_by(cloud_id=cloud_id).first()
                if finding is None:
                    finding = InvestigationFinding(cloud_id=cloud_id, import_id=import_id)
                    session.add(finding)
                for column in _COLUMNS:
                    value = cloud_row.get(column)
                    if column == "visit_date":
                        value = _parse_date(value)
                    setattr(finding, column, value)
                finding.updated_at = _parse_ts(cloud_row.get("updated_at"))
                finding.cloud_version = cloud_row.get("updated_at")  # acknowledge this cloud version
                finding.dirty = 0
                finding.synced_at = now
            session.commit()
        finally:
            session.close()
        logger.info(f"Sync: pulled {len(plan.to_pull)} finding(s) for import_id={import_id}")
        changed = True

    if conflicts:
        logger.warning(f"Sync: {conflicts} finding conflict(s) held locally (dirty) for import_id={import_id}")

    return changed


def push_finding_status(finding_id: int) -> bool:
    """Immediately push one finding after a reviewer status change (so a click
    doesn't wait for the next reconcile), using the same CAS + dirty rules as
    sync_findings_for_import. Returns True only if the cloud acknowledged the
    change; on conflict or failure the finding stays dirty and is retried by
    the ordinary reconcile. No-op (False) in Developer Mode."""
    if is_developer_mode():
        return False

    session = get_session()
    try:
        finding = session.query(InvestigationFinding).filter_by(finding_id=finding_id).first()
        if finding is None:
            return False
        import_cloud_id = _get_import_cloud_id(finding.import_id) if finding.import_id else None
        if import_cloud_id is None:
            return False
        if not finding.cloud_id:
            finding.cloud_id = str(uuid.uuid4())
        cloud_id = finding.cloud_id
        expected = finding.cloud_version
        payload = _cloud_payload(finding, import_cloud_id)
        session.commit()
    finally:
        session.close()

    if expected:
        body = {k: v for k, v in payload.items() if k != "cloud_id"}
        cas = update_row_cas(
            TABLE,
            key_column="cloud_id",
            key_value=cloud_id,
            version_column="updated_at",
            expected_version=expected,
            row=body,
        )
        outcome, new_version = classify_cas_result(cas)
    else:
        result = push_rows(TABLE, [payload], on_conflict="cloud_id")
        if result.success and result.rows:
            outcome, new_version = "acknowledged", result.rows[0].get("updated_at")
        elif result.success:
            outcome, new_version = "error", None  # push returned no row -- treat as unacknowledged
        else:
            outcome, new_version = "error", None

    if outcome == "acknowledged":
        session = get_session()
        try:
            finding = session.query(InvestigationFinding).filter_by(finding_id=finding_id).first()
            if finding is not None:
                finding.cloud_version = new_version
                finding.dirty = 0
                finding.synced_at = datetime.now()
                session.commit()
        finally:
            session.close()
        logger.info(f"Sync: pushed status for finding_id={finding_id}")
        return True

    if outcome == "conflict":
        logger.warning(
            f"Sync CONFLICT on immediate status push for finding_id={finding_id}; kept local change dirty, "
            "cloud NOT overwritten -- the next reconcile will retry."
        )
    else:
        logger.warning(f"Sync: failed to push status for finding_id={finding_id} -- kept dirty for retry")
    return False
