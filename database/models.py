"""SQLAlchemy models for the application's fixed-schema tables.

Note: `raw_visits` is intentionally not modeled here — its columns come
directly from whatever the uploaded Excel file contains (plus the parsed
latitude/longitude columns, and an `import_id` tag), so it is created
dynamically by pandas (see database/import_service.py).
"""

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, Integer, String, UniqueConstraint

from database.connection import Base


class ImportHistory(Base):
    """One row per import. `id` is the import_id referenced by raw_visits'
    and investigation_findings' `import_id` columns, and by ActiveSession."""

    __tablename__ = "import_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_name = Column(String, nullable=False)
    imported_at = Column(DateTime, nullable=False, default=datetime.now)
    rows_imported = Column(Integer, nullable=False)
    duplicates_removed = Column(Integer, nullable=False)
    # Cloud sync bookkeeping (see app/import_sync_service.py). cloud_id is
    # the client-generated UUID tying this import together across laptops --
    # local autoincrement `id` collides across machines. sync_origin is
    # 'local' (this machine ran the import itself) or 'remote' (reconstructed
    # from another machine's push, via app/import_sync_service.py) --
    # 'remote' imports skip local rule evaluation and rely on synced
    # findings instead.
    cloud_id = Column(String, nullable=True, unique=True)
    synced_at = Column(DateTime, nullable=True)
    sync_origin = Column(String, nullable=False, default="local")


class ActiveSession(Base):
    """Tracks the single 'active session file' that analysis, findings, and
    (eventually) email generation operate on. There is always exactly one
    row (id=1); importing a new workbook replaces its import_id, and
    whatever was active before simply stops being used — its data stays in
    the database but is otherwise inert unless explicitly reloaded later."""

    __tablename__ = "active_session"

    id = Column(Integer, primary_key=True)
    import_id = Column(Integer, nullable=True)
    activated_at = Column(DateTime, nullable=True)
    # Cloud sync bookkeeping (see app/import_sync_service.py) -- the shared
    # "which import is the team's current working session" pointer, synced
    # via the path_validator_active_session cloud table.
    import_cloud_id = Column(String, nullable=True)
    synced_at = Column(DateTime, nullable=True)


class RuleParameter(Base):
    """A single named parameter for a named rule, editable from the
    Parameters page. Values are stored as text and cast by whoever reads
    them, since different rules may need different value types.

    Scoped by `environment` ('user' vs 'developer') so User Mode and
    Developer Mode keep completely separate rule thresholds — see
    app/mode_state.py and app/rule_parameters.py. Uniqueness is per
    (environment, rule, parameter)."""

    __tablename__ = "rule_parameters"
    __table_args__ = (
        UniqueConstraint("environment", "rule_name", "parameter_name", name="uq_rule_parameter"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(String, nullable=False, default="user")
    rule_name = Column(String, nullable=False)
    parameter_name = Column(String, nullable=False)
    parameter_value = Column(String, nullable=False)


class FeatureFlag(Base):
    """A named on/off feature toggle, scoped by `environment`. This is the
    core of the Developer Mode system: an experimental feature (starting
    with Hospital Suppression) is enabled in the 'developer' environment
    and disabled in 'user', so production never runs it until the developer
    presses Publish to User Mode (see app/publish_service.py). Adding a new
    experimental feature is just a new flag plus code gated on
    app/feature_flags_service.is_feature_enabled(...)."""

    __tablename__ = "feature_flags"
    __table_args__ = (
        UniqueConstraint("environment", "flag_name", name="uq_feature_flag"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(String, nullable=False)
    flag_name = Column(String, nullable=False)
    enabled = Column(Integer, nullable=False, default=0)  # 0/1


class InvestigationFinding(Base):
    """One row per rule violation flagged for an employee on a given date,
    tagged with the import_id of the session that generated it.

    `status` defaults to "Open" and is set by a reviewer on the Findings
    page (Reviewed/Ignored). Re-running the rule for the same import_id
    carries forward the prior status for any (employee_code, visit_date)
    combination that still matches — see rules/same_location.py. Findings
    from an import_id other than the active session are preserved in the
    database but not shown — see app/findings_service.py.
    """

    __tablename__ = "investigation_findings"

    finding_id = Column(Integer, primary_key=True, autoincrement=True)
    import_id = Column(Integer, nullable=True)
    employee_name = Column(String, nullable=False)
    employee_code = Column(String, nullable=False)
    visit_date = Column(Date, nullable=False)
    rule_name = Column(String, nullable=False)
    message = Column(String, nullable=False)
    # The employee's Division at the time of the flagged visit, straight from
    # the imported call-report's own "Division" column (e.g. Xandra / Onyx /
    # Guardians) -- display only, never used by any rule's clustering or
    # threshold logic. Nullable since findings created before this column
    # existed, or a source file missing the column, have no value to show.
    division = Column(String, nullable=True)
    # Structured stats behind `message`'s prose, used by the HTML manager
    # email to build its human-readable sentences ("9 out of 10 valid
    # GPS-tagged calls...") without parsing them back out of free text.
    # Captured at rule-evaluation time rather than re-read from
    # rule_parameters when the email is built, so the email always reflects
    # the threshold/radius that actually triggered this specific finding
    # even if someone changes those settings afterward. Nullable since not
    # every rule necessarily populates them.
    concentration_percent = Column(Float, nullable=True)
    valid_visit_count = Column(Integer, nullable=True)
    matched_visit_count = Column(Integer, nullable=True)
    radius_meters = Column(Integer, nullable=True)
    threshold_percent = Column(Float, nullable=True)
    # The anchor coordinate of the cluster that caused this finding — the
    # one point whose neighborhood produced matched_visit_count. Captured
    # at rule-evaluation time so Hospital Suppression (see
    # app/hospital_service.py) can check the exact flagged location without
    # re-deriving it later. Nullable for the same reason as the stats above.
    cluster_lat = Column(Float, nullable=True)
    cluster_lon = Column(Float, nullable=True)
    # Hospital Suppression is a post-processing filter, not part of rule
    # evaluation — it only ever runs against findings that are already
    # flagged, and only decides whether an automatic email goes out.
    # notification_status: null (not yet processed) | "Sent" |
    # "Hospital Suppressed" | "Failed". suppression_reason is the
    # human-readable one-liner ("Apollo Hospital (63 meters away)"); the
    # four columns below are the same decision broken into structured
    # fields for the Findings page's suppression detail panel, set only
    # when notification_status is "Hospital Suppressed".
    notification_status = Column(String, nullable=True)
    suppression_reason = Column(String, nullable=True)
    hospital_name = Column(String, nullable=True)
    hospital_lat = Column(Float, nullable=True)
    hospital_lon = Column(Float, nullable=True)
    hospital_distance_meters = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="Open")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    # Cloud sync bookkeeping (see app/findings_sync_service.py). updated_at
    # is bumped on every reviewer status change (set_status()/
    # set_notification_status() in app/findings_service.py) -- the field the
    # sync poller's delta-pull relies on to fetch only what changed since a
    # laptop's own high-water mark, so a status click on one laptop reaches
    # another without re-pulling every finding every tick.
    cloud_id = Column(String, nullable=True, unique=True)
    updated_at = Column(DateTime, nullable=True)
    synced_at = Column(DateTime, nullable=True)
    # --- Sync reliability (Phase 1 -- Path Validator findings only) --------
    # cloud_version is the cloud row's own `updated_at` token this local row
    # has last ACKNOWLEDGED (stored verbatim from a push ack or a pull). It is
    # a server-origin value compared ONLY against the cloud's current
    # `updated_at`, never against a local wall clock / datetime.now(), so
    # timezone or clock skew can't reverse a sync decision. `dirty` = 1 marks
    # a local business mutation (suppression, reviewer status) not yet
    # acknowledged by the cloud: reconcile_rows never pulls a cloud snapshot
    # over a dirty row, and the push uses an optimistic version check (CAS)
    # against cloud_version. See app/findings_sync_service.py and
    # app/sync_service.reconcile_rows(). Persisted columns, so an
    # unsynchronized change survives a restart/crash and is retried.
    cloud_version = Column(String, nullable=True)
    dirty = Column(Integer, nullable=False, default=0)


class WorkbookConnection(Base):
    """The selected file path for one of the named hierarchy workbooks
    (Onyx, Guardians, Xandra). This is independent of the daily call-data
    import — it points at separate, not-yet-provided hierarchy files."""

    __tablename__ = "workbook_connections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workbook_name = Column(String, nullable=False, unique=True)
    file_path = Column(String, nullable=True)
    # Cloud sync bookkeeping (see app/organization_data_sync_service.py).
    # storage_path is this workbook's object path in the
    # 'path-validator-organization-data' Storage bucket once pushed;
    # cloud_updated_at mirrors the cloud row's own updated_at so the sync
    # poller can tell whether another laptop has connected a newer version.
    # file_path itself is untouched -- still just "what was last manually
    # browsed on this machine."
    storage_path = Column(String, nullable=True)
    cloud_updated_at = Column(DateTime, nullable=True)
    synced_at = Column(DateTime, nullable=True)
    # The genuine local "last modified" timestamp the app-wide
    # Last-Modified-Wins rule (see app/sync_service.reconcile_rows(),
    # Milestone 35) compares against the cloud's own updated_at -- bumped
    # in app/workbook_connections.set_connection() every time this
    # workbook is (re)connected, whether by a local Browse click or by
    # applying an inbound sync pull. Distinct from cloud_updated_at, which
    # only ever records the cloud's own timestamp as of the last sync.
    updated_at = Column(DateTime, nullable=True)


class GeocodeCache(Base):
    """Caches a resolved address per coordinate pair so the same GPS point
    is never sent to the reverse-geocoding service twice. Populated only at
    email-generation time — never during import or rule evaluation."""

    __tablename__ = "geocode_cache"
    __table_args__ = (UniqueConstraint("latitude", "longitude", name="uq_geocode_cache_lat_lon"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    address = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class HospitalLookupCache(Base):
    """Caches whether a hospital/medical facility was found near a given
    (rounded) coordinate, so the same flagged cluster is never queried
    against the Overpass API twice — see app/hospital_service.py. Populated
    only for findings that are already flagged, at email-generation time,
    same as GeocodeCache. Only genuine lookups (a real answer from Overpass,
    found or not) are cached — a network/lookup failure is never cached, so
    a transient outage gets retried on the next run instead of being
    permanently remembered as "no hospital"."""

    __tablename__ = "hospital_lookup_cache"
    __table_args__ = (UniqueConstraint("latitude", "longitude", name="uq_hospital_lookup_cache_lat_lon"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    found = Column(Integer, nullable=False)  # 0/1
    hospital_name = Column(String, nullable=True)
    hospital_lat = Column(Float, nullable=True)
    hospital_lon = Column(Float, nullable=True)
    distance_meters = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class EmailNotification(Base):
    """One row per manager notification prepared for a set of findings in
    an import session. `finding_ids` is a comma-separated list of the
    investigation_findings.finding_id values covered by this email.

    status: Draft (built but not yet sent) | Sent | Failed (SMTP error, see
    error_message) | Unresolved (no manager/email could be found at all)."""

    __tablename__ = "email_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_id = Column(Integer, nullable=True)
    manager_name = Column(String, nullable=True)
    manager_email = Column(String, nullable=True)
    subject = Column(String, nullable=False)
    body = Column(String, nullable=False)
    finding_ids = Column(String, nullable=False)
    status = Column(String, nullable=False, default="Draft")
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    sent_at = Column(DateTime, nullable=True)
    # Cloud sync bookkeeping (see app/email_sync_service.py) -- same
    # updated_at-driven delta-pull treatment as InvestigationFinding.
    cloud_id = Column(String, nullable=True, unique=True)
    updated_at = Column(DateTime, nullable=True)
    synced_at = Column(DateTime, nullable=True)


class MasterEmailRecipient(Base):
    """One manually-configured recipient for the Path Validator Master
    Email system (see app/master_email_recipients_service.py,
    app/notification_service.py) -- replaces the old single
    AppSettings.master_email_address field with a proper recipient table,
    built using InventoryEmailRecipient (database/models.py, above) as its
    explicit reference architecture, per instruction.

    Individual manager emails (one per RBM, resolved dynamically by
    walking the employee hierarchy -- app.hierarchy_service.compute_seniors())
    are completely unaffected by this table and unchanged by its
    introduction; this table only feeds the SEPARATE, ADDITIONAL master
    consolidated report every automatic run also sends.

    Unlike InventoryEmailRecipient.divisions (a comma-separated list --
    Inventory recipients commonly want more than one division combined),
    `division` here is a SINGLE value, matching the task's own "selectable
    Division filter" wording (one recipient = one filter): "ALL" (every
    division, the old single-recipient behavior) or one of the real,
    confirmed Division values Path Validator's own findings actually carry
    (InvestigationFinding.division, sourced straight from the uploaded
    call-report's own "Division" column -- confirmed against live data as
    "Onyx" / "Guardians" / "Xandra", Title Case, NOT the "Onyx"/"Guardian"/
    "Zandra" spelling that shows up in task examples -- same discrepancy
    already flagged once before, see
    app.inventory_email_recipients_service.DIVISION_OPTIONS's own
    docstring). Stored uppercase ("ONYX"/"GUARDIANS"/"XANDRA") for a
    stable, case-normalized comparison key; matching against a finding's
    own `division` value is always done case-insensitively (see
    app.master_email_recipients_service.division_matches) since the
    uploaded workbook's own casing is not guaranteed.

    Always stored on the config/main database (get_config_session()) --
    same reasoning as InventoryEmailRecipient: this is configuration, not
    per-session data, and must be visible regardless of Developer/User
    Mode."""

    __tablename__ = "master_email_recipients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    division = Column(String, nullable=False, default="ALL")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=True)


class AppSettings(Base):
    """Application settings, one row per `environment` ('user' vs
    'developer') so Developer Mode and User Mode keep completely separate
    credentials/config — see app/mode_state.py, app/email_settings_service.py,
    app/geoapify_settings_service.py. `gmail_app_password` is a Gmail App
    Password, stored as entered — this is a local desktop app with no
    external credential store to delegate to.

    Two fields are deliberately GLOBAL rather than per-environment and are
    only ever read/written on the 'user' row: `setup_completed` (onboarding
    is app-wide) and `dev_password_hash`/`dev_password_salt` (the gate into
    Developer Mode itself). See app/app_state_service.py and
    app/dev_auth_service.py."""

    __tablename__ = "app_settings"
    __table_args__ = (
        UniqueConstraint("environment", name="uq_app_settings_environment"),
    )

    id = Column(Integer, primary_key=True)
    environment = Column(String, nullable=False, default="user")
    sender_gmail_address = Column(String, nullable=True)
    gmail_app_password = Column(String, nullable=True)
    automatic_email_enabled = Column(Integer, nullable=False, default=0)  # 0/1
    # Recipient of the consolidated "every flagged employee this session"
    # master report (see app/notification_service.py). Defaults to
    # gddesk@saffronformulations.com but is editable from the Settings page.
    master_email_address = Column(String, nullable=True)
    # Geoapify Places API key used by Hospital Suppression (see
    # app/hospital_service.py, app/geoapify_settings_service.py) — replaces
    # the free, unauthenticated OpenStreetMap Overpass API, which repeatedly
    # proved unreliable in production (rate limits, full outages, slow
    # HTTP 504s) despite being logically correct when it did respond.
    geoapify_api_key = Column(String, nullable=True)
    # GLOBAL (user row only): whether the first-run Setup Wizard
    # (ui/setup_wizard.py) has been completed at least once.
    setup_completed = Column(Integer, nullable=False, default=0)  # 0/1
    # GLOBAL (user row only): salted PBKDF2 hash of the Developer Mode
    # password. Never the plaintext, never hardcoded — set/changed from
    # inside Developer Mode (see app/dev_auth_service.py).
    dev_password_hash = Column(String, nullable=True)
    dev_password_salt = Column(String, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class InventoryParameter(Base):
    """A single named parameter for Inventory Monitoring (see
    app/inventory_parameters_service.py) -- currently just the
    replenishment threshold multiplier, editable from the Inventory
    Settings page. Deliberately separate from RuleParameter (Path
    Validator's Developer/User Mode-scoped store) and
    PaymentAnalyticsParameter (its own module) -- Inventory Monitoring
    has no Developer Mode concept of its own, same reasoning as
    InventoryThreshold below.

    Always stored on the config/main database (get_config_session()),
    same as InventoryThreshold."""

    __tablename__ = "inventory_parameters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parameter_name = Column(String, nullable=False, unique=True)
    parameter_value = Column(String, nullable=False)


class InventoryThreshold(Base):
    """One row per (branch_key, item_key) — the Inventory Monitoring
    module's replenishment threshold, generated from an uploaded Previous
    Month Sales Report in its current compact format (Division, CFA, Item
    Name, Packing, Sales -- see app/excel_validation.py's
    SALES_REPORT_REQUIRED_COLUMNS and app/threshold_service.py). Keyed by
    item_name rather than item_code because this report format carries no
    item code at all; `branch_location` holds the report's own "CFA"
    column value (same field, carried over from the pre-CFA format's
    "BranchLocation" naming so app/replenishment_service.py's matching
    logic didn't need to change).

    Full-replacement table: a Previous Month Sales Report upload is a
    complete monthly snapshot, not an incremental delta -- every row is
    deleted and rebuilt from the uploaded file on each upload (see
    app/threshold_service.generate_thresholds_from_sales()). A CFA+item
    combination absent from the newest upload no longer has a row at all,
    instead of lingering with a previous upload's stale sales/threshold
    numbers.

    branch_key/item_key are the case/whitespace-normalized forms of
    branch_location/item_name (see app/threshold_service._normalize_match_key)
    used for ALL matching -- both the upsert above and the cross-file
    lookup against an uploaded Inventory Report
    (app/replenishment_service.py). branch_location/item_name themselves
    keep their original spelling/casing and are used for display only.
    This split exists because the Sales Report and the Inventory Report
    are two independently-typed files that routinely spell "the same" CFA
    or item slightly differently (case, extra spaces) -- matching on the
    raw display strings silently drops every such row from evaluation,
    which is why the Inventory Dashboard could show "0 Products
    Evaluated" even after a successful upload.

    raw_threshold = previous_month_sales * the Inventory Threshold
    Multiplier (app/inventory_parameters_service.py), unrounded.
    packed_threshold = raw_threshold rounded UP to the nearest multiple of
    `packing` -- see app/threshold_service.py's packed-threshold formula.
    Replenishment (app/replenishment_service.py) always compares stock
    against packed_threshold, never raw_threshold; both are kept so the
    Threshold Display Mode setting (Raw Quantity vs Packs) can show either
    without recomputing.

    Always stored on the config/main database (get_config_session()),
    never the mode-dependent get_session() -- Inventory Monitoring has no
    Developer Mode concept of its own, so its data must not be silently
    redirected into the dev-only database file just because Path
    Validator's Developer Mode happens to be active."""

    __tablename__ = "inventory_thresholds"
    __table_args__ = (
        UniqueConstraint("branch_key", "item_key", name="uq_inventory_threshold_branch_item"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    branch_location = Column(String, nullable=False)
    branch_key = Column(String, nullable=False)
    division = Column(String, nullable=True)
    item_name = Column(String, nullable=False)
    item_key = Column(String, nullable=False)
    packing = Column(Float, nullable=False)
    previous_month_sales = Column(Float, nullable=False)
    raw_threshold = Column(Float, nullable=False)
    packed_threshold = Column(Float, nullable=False)
    last_updated = Column(DateTime, nullable=False, default=datetime.now)


class PaymentInvoice(Base):
    """One row per invoice line from an uploaded Historical or Monthly
    Payment Report (see app/payment_analytics_service.py) -- the
    permanent raw ledger every customer profile's aggregates are computed
    from. A row is never deleted once stored, even after its month rolls
    out of the rolling six-month window -- see payment_active_months
    below, which (not any flag here) decides which invoices are currently
    "active" vs. archived.

    `year`/`month_number` are the canonical, unambiguous ordering key
    (derived from lr_date, not parsed from the free-text `month` column --
    see app/payment_analytics_service.py's module docstring) used for
    every rolling-window comparison. `month` is kept purely as the
    original display label from the uploaded file's own Month column.

    `payment_days` (Clear Date - LR Date) is precomputed at load time so
    every downstream aggregate reads it directly instead of re-deriving it
    from the two dates repeatedly.

    Always stored on the config/main database (get_config_session()), same
    as Inventory Monitoring's tables -- Payment Analytics has no Developer
    Mode concept of its own."""

    __tablename__ = "payment_invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    month = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    month_number = Column(Integer, nullable=True)
    party_name = Column(String, nullable=False)
    customer_type = Column(String, nullable=True)
    invoice_no = Column(String, nullable=True)
    lr_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=True)
    clear_date = Column(Date, nullable=False)
    payment_days = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    # Cloud sync bookkeeping (see app/payment_sync_service.py). updated_at
    # is set once at insert time and never bumped again -- these rows are
    # immutable once stored (only created via a monthly append, or wiped
    # wholesale by a Historical Report re-run), so it doubles as the
    # delta-pull watermark with no separate "was this edited" tracking
    # needed.
    cloud_id = Column(String, nullable=True, unique=True)
    updated_at = Column(DateTime, nullable=True)
    synced_at = Column(DateTime, nullable=True)


class PaymentAnalyticsParameter(Base):
    """A single named parameter for a named rule in the Payment Analytics
    module (see app/payment_parameters_service.py) -- Historical Payment
    Analytics' risk-scoring thresholds and Collections Action Center's
    overdue-ageing buckets, editable from the Parameters page.

    Deliberately separate from RuleParameter (Path Validator's
    Developer/User Mode-scoped parameter table): Payment Analytics has no
    Developer Mode concept of its own, so its parameters are never split
    by environment -- there is exactly one value per (rule_name,
    parameter_name), always.

    Always stored on the config/main database (get_config_session())."""

    __tablename__ = "payment_analytics_parameters"
    __table_args__ = (
        UniqueConstraint("rule_name", "parameter_name", name="uq_payment_analytics_parameter"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_name = Column(String, nullable=False)
    parameter_name = Column(String, nullable=False)
    parameter_value = Column(String, nullable=False)


class PaymentActiveMonth(Base):
    """One row per month currently in the rolling six-month active window
    (see app/payment_analytics_service.py). Membership here -- not a flag
    on PaymentInvoice -- is the single source of truth for which invoices
    currently feed the Dashboard/Customer Analytics calculations; an
    invoice whose (year, month_number) has no matching row here is
    archived: kept in payment_invoices, but excluded from every
    aggregate.

    At most 6 rows at any time once the window is full; fewer only while
    the historical baseline (or the rolling window) hasn't accumulated 6
    months yet.

    Always stored on the config/main database (get_config_session()), same
    as PaymentInvoice/PaymentCustomerProfile."""

    __tablename__ = "payment_active_months"
    __table_args__ = (UniqueConstraint("year", "month_number", name="uq_payment_active_month"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False)
    month_number = Column(Integer, nullable=False)  # 1-12
    month_label = Column(String, nullable=False)  # display text, from the uploaded file's own Month column
    added_at = Column(DateTime, nullable=False, default=datetime.now)


class PaymentCustomerProfile(Base):
    """One row per Party Name -- the aggregated customer profile computed
    from payment_invoices (see app/payment_analytics_service.py). Fully
    recalculated (delete-all + rebuild) every time the historical ledger
    changes, so this table always reflects exactly what's currently in
    payment_invoices; never accumulates stale rows for a customer who no
    longer has any invoices.

    risk_category: "Green" (< 30 days), "Yellow" (30-44), "Orange"
    (45-59), "Red" (>= 60) -- see payment_analytics_service.classify_risk().

    Always stored on the config/main database (get_config_session()), same
    reasoning as PaymentInvoice."""

    __tablename__ = "payment_customer_profiles"
    __table_args__ = (UniqueConstraint("party_name", name="uq_payment_customer_profile_party_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    party_name = Column(String, nullable=False)
    customer_type = Column(String, nullable=True)
    total_invoices = Column(Integer, nullable=False)
    average_payment_days = Column(Float, nullable=False)
    earliest_invoice = Column(Date, nullable=True)
    latest_invoice = Column(Date, nullable=True)
    risk_category = Column(String, nullable=False)
    last_updated = Column(DateTime, nullable=False, default=datetime.now)


class OutstandingInvoice(Base):
    """One row per invoice from the currently active Outstanding Report
    (see app/collections_service.py) -- the Collections Action Center's
    daily working set. Fully replaced (delete-all + reinsert) every time
    a new Outstanding Report is uploaded, since only one such report is
    ever active at a time; this also resets every `followed_up` flag,
    matching the "Daily Refresh" behavior the page is built around.

    Days Outstanding, Days Over Due, and Status are deliberately NOT
    columns here -- they depend on the current system date, which moves
    forward every day even without a new upload, so they're always
    computed fresh at read time (see
    collections_service.compute_days_and_status()) rather than frozen at
    upload time.

    Always stored on the config/main database (get_config_session()),
    same as the rest of Payment Analytics -- no Developer Mode concept of
    its own.

    Column names here are internal/stable; the real Outstanding Report's
    actual header text (Division, H.Q., Bill No., L.R. Date, Bill Amount,
    Month -- see app/excel_validation.OUTSTANDING_REPORT_REQUIRED_COLUMNS)
    is only ever matched against at upload time, in
    app/collections_service.process_outstanding_report()."""

    __tablename__ = "outstanding_invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    party_name = Column(String, nullable=False)
    team = Column(String, nullable=True)  # from the report's "Division" column
    hq = Column(String, nullable=True)  # from the report's "H.Q." column
    invoice_no = Column(String, nullable=True)  # from the report's "Bill No." column
    lr_date = Column(Date, nullable=False)  # from the report's "L.R. Date" column
    due_date = Column(Date, nullable=False)
    bill_amount = Column(Float, nullable=True)  # from the report's "Bill Amount" column
    month = Column(String, nullable=True)  # from the report's "Month" column -- stored for future use, not surfaced yet
    followed_up = Column(Integer, nullable=False, default=0)  # 0/1
    uploaded_at = Column(DateTime, nullable=False, default=datetime.now)
    # Cloud sync bookkeeping (see app/payment_sync_service.py). cloud_id
    # exists solely so the followed_up checkbox toggle can address one
    # specific row across machines between uploads -- every full upload
    # is synced as a full replace (delete-all-cloud + push-current) in
    # both directions, never a delta, matching this table's own
    # local "Daily Refresh" behavior.
    cloud_id = Column(String, nullable=True, unique=True)
    synced_at = Column(DateTime, nullable=True)


class InventoryReplenishment(Base):
    """One row per (branch_key, item_key) — the Inventory Monitoring
    module's replenishment evaluation, generated from an uploaded
    Inventory Report compared against the stored InventoryThreshold
    records (see app/replenishment_service.py).

    IMPORTANT: branch_key here means something subtly different from
    InventoryThreshold.branch_key. InventoryThreshold's branch_key
    collapses to the CFA level (see its docstring and
    app/threshold_service.normalize_branch_match_key) because that's the
    granularity the Sales Report defines thresholds at. This table's
    branch_key does NOT collapse to CFA level -- it's the plain
    case/whitespace-normalized (app/threshold_service.normalize_match_key)
    form of the Inventory Report's own, more granular BranchLocation.
    Reason: a single CFA commonly has multiple physical stock locations in
    the Inventory Report (e.g. "AHMEDABAD (C&F)" and "AHMEDABAD (HEAD
    OFFICE)" both belong to CFA "AHMEDABAD") that share one CFA-level
    threshold but have independently tracked stock -- collapsing them here
    too would silently merge two different warehouses' stock levels into
    one row, hiding which physical location actually needs replenishment.
    item_key, by contrast, IS the same normalize_match_key used by
    InventoryThreshold, since item names aren't similarly split by
    location.

    Full-replacement table: an Inventory Report upload is a complete
    monthly snapshot, not an incremental delta -- every row is deleted
    and rebuilt from the uploaded file (matched against the current
    InventoryThreshold) on each upload (see
    app/replenishment_service.evaluate_replenishment()). A physical
    branch+item combination absent from the newest upload, or that no
    longer matches a threshold, no longer has a row at all, instead of
    lingering with a previous upload's stale stock figures.

    `item_code` is still populated (nullable) straight from the Inventory
    Report's own row -- that upload format is unchanged and still carries
    it -- but it is purely informational display now, never part of the
    match/uniqueness key. `division` replaces the old `item_group`: the
    Sales Report no longer has an Item Group column, so the matched
    threshold's Division is shown in its place.

    stock_deficit and the replenishment decision (status) are always
    computed against `packed_threshold`, never `raw_threshold` -- see
    InventoryThreshold's docstring.

    Stores every evaluated product, not just ones needing replenishment
    -- status is "Replenishment Required" or "Healthy" -- so the
    Dashboard can report accurate totals across all three counts. The
    Replenishment page itself only ever queries the "Replenishment
    Required" subset.

    Always stored on the config/main database (get_config_session()),
    same reasoning as InventoryThreshold -- Inventory Monitoring has no
    Developer Mode concept of its own."""

    __tablename__ = "inventory_replenishment"
    __table_args__ = (
        UniqueConstraint("branch_key", "item_key", name="uq_inventory_replenishment_branch_item"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    branch_location = Column(String, nullable=False)
    branch_key = Column(String, nullable=False)
    division = Column(String, nullable=True)
    item_code = Column(String, nullable=True)
    item_name = Column(String, nullable=False)
    item_key = Column(String, nullable=False)
    packing = Column(Float, nullable=True)
    closing_stock = Column(Float, nullable=False)
    transit_stock = Column(Float, nullable=False)
    effective_available_stock = Column(Float, nullable=False)
    raw_threshold = Column(Float, nullable=False)
    packed_threshold = Column(Float, nullable=False)
    stock_deficit = Column(Float, nullable=False)
    status = Column(String, nullable=False)  # "Replenishment Required" | "Healthy"
    last_updated = Column(DateTime, nullable=False, default=datetime.now)


class CwhStock(Base):
    """One row per item_key — the Central Warehouse (CWH) page's full
    evaluated snapshot for that SKU, generated once per Inventory Report
    "processing run" (app/cwh_service.evaluate_cwh_stock(), called from
    the same Inventory Report upload that feeds InventoryReplenishment)
    and simply read back as-is by the page thereafter -- never
    recalculated live at page-view time. Mirrors InventoryReplenishment's
    own shape (stores the full comparison, not just raw stock) for
    exactly the same reason: so a later change to an input (the CWH
    Threshold Multiplier -- app/inventory_parameters_service.py) can
    never retroactively alter an already-generated snapshot; only the
    next processing run picks up a changed multiplier.

    closing_stock/transit_stock come from the Ahmedabad CWH rows of the
    uploaded Inventory Report -- the same rows InventoryReplenishment
    deliberately excludes (see app/threshold_service.is_ahmedabad_cwh_branch
    and app/replenishment_service.py's Phase 1 exclusion, which must NOT
    change) rather than the rows it keeps. closing_stock is what the
    Central Warehouse page calls "Physical Stock"; transit_stock is kept
    alongside it, mirroring InventoryReplenishment's own closing/transit
    split, purely for display/future use -- it is NOT added into
    "Physical Stock" (stock in transit to CWH is not yet physically
    there).

    Full-replacement table (an Inventory Report upload is a complete
    monthly snapshot, not an incremental delta -- see
    app/cwh_service.evaluate_cwh_stock()): every row is deleted and
    rebuilt on each run. An item still carrying CFA demand (present in
    InventoryThreshold) but not mentioned at CWH in this particular
    upload still gets a row with closing_stock/transit_stock reset to 0
    (0 physical stock is a real, current shortage, not something to
    carry forward from a stale prior snapshot); an item with neither CFA
    demand nor CWH stock in this upload gets no row at all.
    total_previous_month_sales/cwh_threshold/surplus_deficit/status are
    always recomputed fresh for every surviving row, using the current
    CWH Threshold Multiplier.

    total_previous_month_sales is the sum of InventoryThreshold.
    previous_month_sales across every CFA's branch_key for this item_key,
    AS OF the processing run that generated this row -- since Phase 1
    guarantees InventoryThreshold never contains an Ahmedabad CWH row,
    this is already, by construction, "every CFA except Ahmedabad CWH".
    cwh_threshold = total_previous_month_sales × the CWH Threshold
    Multiplier at that same moment. surplus_deficit = closing_stock −
    cwh_threshold. status is "Healthy" / "Shortage" (see
    app/cwh_service._cwh_status).

    Unlike InventoryThreshold/InventoryReplenishment, this is not keyed by
    (branch_key, item_key) -- there is exactly one CWH, so item_key alone
    is the natural key (see app/threshold_service.normalize_match_key,
    the same normalization InventoryThreshold/InventoryReplenishment
    already use for item_name, so this table joins against them by
    item_key with no extra translation). item_code is carried from the
    Inventory Report's own row, same as InventoryReplenishment.item_code
    -- informational display only.

    last_updated is bumped on every upsert, following the exact same
    convention as InventoryThreshold/InventoryReplenishment's own
    last_updated columns -- kept deliberately even though no cloud sync
    exists for this table yet, so a future sync_cwh_stock() can reuse the
    same Last-Modified-Wins reconciliation (app/sync_service.reconcile_rows)
    those two tables already use, without a schema change.

    Always stored on the config/main database (get_config_session()),
    same reasoning as InventoryThreshold/InventoryReplenishment."""

    __tablename__ = "cwh_stock"
    __table_args__ = (
        UniqueConstraint("item_key", name="uq_cwh_stock_item"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_code = Column(String, nullable=True)
    item_name = Column(String, nullable=False)
    item_key = Column(String, nullable=False)
    closing_stock = Column(Float, nullable=False)
    transit_stock = Column(Float, nullable=False)
    total_previous_month_sales = Column(Float, nullable=False, default=0.0)
    cwh_threshold = Column(Float, nullable=False, default=0.0)
    surplus_deficit = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="Healthy")  # "Healthy" | "Shortage"
    last_updated = Column(DateTime, nullable=False, default=datetime.now)


class InventoryEmailRecipient(Base):
    """One manually-configured recipient for the Inventory Automated Email
    System (see app/inventory_notification_service.py) -- Version 2.0,
    Milestone 56, built with the Path Validator Automated Email System as
    its reference architecture.

    Path Validator resolves recipients dynamically at send time by walking
    the employee hierarchy (see app.hierarchy_service.compute_seniors());
    Inventory has no such per-employee hierarchy to resolve FROM, so its
    recipients are instead configured directly here, once, from the
    Automated Emails page: a name, an email address, and which Division(s)
    they should receive Replenishment reports for. A recipient with more
    than one division checked receives ONE combined email covering every
    one of their selected divisions, not one email per division.

    `divisions` is a comma-separated list of the real Division values
    already present in InventoryThreshold/InventoryReplenishment data
    (currently "GUARDIANS", "ONYX", "XANDRA" -- see
    app.inventory_email_recipients_service.DIVISION_OPTIONS) -- the same
    simple comma-separated-string convention EmailNotification.finding_ids
    already uses above, not a separate join table, since the division set
    is small and fixed.

    Always stored on the config/main database (get_config_session()) --
    Inventory Monitoring has no Developer Mode concept of its own, same
    reasoning as InventoryParameter/InventoryThreshold above."""

    __tablename__ = "inventory_email_recipients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    divisions = Column(String, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=True)


class WorkDistributionParameter(Base):
    """A single named KPI threshold for Work Distribution (see
    app/work_distribution_parameters_service.py) -- BM/ABM coverage
    thresholds, editable from the Work Distribution Settings page.
    Deliberately separate from RuleParameter/InventoryParameter/
    PaymentAnalyticsParameter -- Work Distribution has no Developer Mode
    concept of its own, same reasoning as InventoryParameter above.

    Always stored on the config/main database (get_config_session())."""

    __tablename__ = "work_distribution_parameters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parameter_name = Column(String, nullable=False, unique=True)
    parameter_value = Column(String, nullable=False)


class WorkDistributionDoctor(Base):
    """One row per doctor from the most recently uploaded Work Distribution
    (RGD coverage) report -- full-replacement table (see
    app/work_distribution_service.py): every row is deleted and rebuilt on
    each upload, since the report is a complete monthly snapshot, not an
    incremental delta.

    A doctor always belongs to exactly one BM (`bm`); `abm` is only
    meaningful for ABM KPI purposes when `abm_rgd` normalizes to "A-RGD"
    (see app.work_distribution_service._is_a_rgd) -- a blank or
    non-"A-RGD" value excludes this doctor from ABM calculations entirely,
    even though `abm`/`abm_visit_count` are still stored either way so the
    raw uploaded data is always fully preserved.

    bm_visit_count/abm_visit_count are already-counted integers (see
    app/work_distribution_parser.py's month-agnostic visit-date-list
    parsing -- "04,12,25" -> 3), not the raw comma-separated date text,
    which is not needed for anything downstream.

    Always stored on the config/main database (get_config_session()) --
    Work Distribution has no Developer Mode concept of its own."""

    __tablename__ = "work_distribution_doctors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    division = Column(String, nullable=True)
    doctor_code = Column(String, nullable=True)
    doctor_name = Column(String, nullable=False)
    speciality = Column(String, nullable=True)
    category = Column(String, nullable=True)
    abm_rgd = Column(String, nullable=True)
    city = Column(String, nullable=True)
    hq = Column(String, nullable=True)
    region = Column(String, nullable=True)
    bm = Column(String, nullable=True)
    abm = Column(String, nullable=True)
    bm_visit_count = Column(Integer, nullable=False, default=0)
    abm_visit_count = Column(Integer, nullable=False, default=0)
    period_label = Column(String, nullable=True)
    last_updated = Column(DateTime, nullable=False, default=datetime.now)


class WorkDistributionFinding(Base):
    """One row per employee (BM or ABM) -- the Work Distribution module's
    computed KPI result for that employee, generated from
    WorkDistributionDoctor (see app/work_distribution_service.py).
    Full-replacement table, same reasoning as WorkDistributionDoctor.

    designation: "BM" | "ABM". total_calls/missed_doctors/
    poor_coverage_doctors are computed using that designation's own
    visit-count column (bm_visit_count for BM; abm_visit_count for
    ABM-eligible "A-RGD" doctors only) -- see WorkDistributionDoctor's own
    docstring.

    status: "Healthy" | "Flagged" -- evaluated against the
    app.work_distribution_parameters_service thresholds in effect AT THE
    TIME OF THIS UPLOAD, the same "snapshot, not live recompute"
    convention InventoryThreshold/InventoryReplenishment already use:
    changing a KPI parameter afterward does not retroactively alter an
    already-generated finding, only the next upload does.

    Always stored on the config/main database (get_config_session())."""

    __tablename__ = "work_distribution_findings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_name = Column(String, nullable=False)
    designation = Column(String, nullable=False)  # "BM" | "ABM"
    division = Column(String, nullable=True)
    total_doctors = Column(Integer, nullable=False, default=0)
    total_calls = Column(Integer, nullable=False, default=0)
    missed_doctors = Column(Integer, nullable=False, default=0)
    poor_coverage_doctors = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="Healthy")
    reason = Column(String, nullable=True)
    last_updated = Column(DateTime, nullable=False, default=datetime.now)


class ManagerWorkAllocationParameter(Base):
    """A single named threshold for the Manager Work Allocation engine
    (see app/manager_work_allocation_parameters_service.py) -- e.g. Minimum
    Joint Working Days for the ABM engine (this phase); RBM's own
    threshold(s) land here too once that phase is built. Deliberately a
    SEPARATE table from WorkDistributionParameter -- Manager Work
    Allocation is a completely independent engine from RGD Coverage (own
    upload, own calculation, own findings), so its settings storage stays
    independent too rather than sharing RGD Coverage's parameter table.

    Always stored on the config/main database (get_config_session())."""

    __tablename__ = "manager_work_allocation_parameters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parameter_name = Column(String, nullable=False, unique=True)
    parameter_value = Column(String, nullable=False)


class ManagerWorkAllocationRecord(Base):
    """Rolling six-month monthly history for the Manager Work Allocation
    engines -- ONE ROW PER (source_engine, emp_code, team_emp_code, month),
    i.e. one row per manager/subordinate pair PER MONTH, not per uploaded
    report row.

    Redesigned 2026-08-05 (a genuine business-process correction, not a
    bug fix): what looked like duplicate manager/subordinate rows within
    one upload were never duplicates at all -- each uploaded report already
    contains several months of history (Feb, Mar, Apr, ...), and a pair
    appearing more than once simply means separate monthly records for
    that same pair. The table used to be a full-replacement mirror of "the
    last upload" (delete everything, reinsert); it is now a genuinely
    PERSISTENT, ACCUMULATING store spanning uploads -- see
    app.manager_work_allocation_shared.sync_rolling_window, the one place
    that ever writes to it (both engines call the same function): each
    upload UPSERTS whatever (pair, month) records it contains (an
    already-known month gets its joint_days corrected/replaced, not
    duplicated), then the table is trimmed to keep only the newest 6
    DISTINCT months by month value -- never by upload order, filename, or
    manual replacement (see that function's own docstring for the exact
    trim algorithm). Rows for a genuine intra-month duplicate (same pair,
    same month, split across >1 raw upload row) ARE still summed at
    upsert time -- see app.manager_work_allocation_shared.merge_same_month_duplicates
    -- that part of the old "duplicate merging" logic was correct, it was
    only ever merging ACROSS months that was the bug.

    month is the raw display label as normalized by `parse_month` (e.g.
    "Jul-2026", regardless of how the source file spelled it -- "July
    2026", "07-2026", etc. all normalize the same way). month_sort_key is
    year*100+month_number (e.g. 202607) -- the single sortable integer
    both the trim algorithm and any ORDER BY in this app use, so "newest
    6 months" is always a plain integer comparison, never a string-sort
    footgun.

    joint_days is this (pair, month)'s own already-merged total -- see
    module docstring above. Downstream, ABM averages this across every
    retained month for a pair; RBM sums it the same way it always did
    (that engine's own coverage rule -- "covered" if any joint work
    happened -- never changed, only which months get summed into it).

    rep_hq/zone/region/team_emp_hq/dates_spent_in_joint/general/b_rgd/
    total_dr/covered_dr/general_covered/b_rgd_covered are per-row
    descriptive fields imported and stored per the module's own spec, not
    used by any calculation -- on an intra-month merge, the first
    non-blank value across the merged rows is kept, same
    `first_nonblank` convention used for every other descriptive field
    here.

    source_engine: "ABM" | "RBM" -- which engine's own upload/Run Analysis
    call owns this row (see app.manager_work_allocation_service and
    app.manager_work_allocation_rbm_service, both of which scope every
    read/write here to `source_engine == <their own value>` -- the ABM and
    RBM engines upload separate files and must never see or trim each
    other's history).

    Always stored on the config/main database (get_config_session()) --
    Manager Work Allocation has no Developer Mode concept of its own."""

    __tablename__ = "manager_work_allocation_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    division = Column(String, nullable=True)
    emp_code = Column(String, nullable=True)
    emp_name = Column(String, nullable=False)
    emp_designation = Column(String, nullable=True)
    month = Column(String, nullable=True)
    month_sort_key = Column(Integer, nullable=True)
    team_emp_code = Column(String, nullable=True)
    team_emp_name = Column(String, nullable=False)
    team_emp_designation = Column(String, nullable=True)
    joint_days = Column(Integer, nullable=False, default=0)
    rep_hq = Column(String, nullable=True)
    zone = Column(String, nullable=True)
    region = Column(String, nullable=True)
    team_emp_hq = Column(String, nullable=True)
    total_visits_done_in_joint = Column(Integer, nullable=True)
    dates_spent_in_joint = Column(String, nullable=True)
    general = Column(String, nullable=True)
    b_rgd = Column(String, nullable=True)
    total_dr = Column(String, nullable=True)
    covered_dr = Column(String, nullable=True)
    general_covered = Column(String, nullable=True)
    b_rgd_covered = Column(String, nullable=True)
    source_engine = Column(String, nullable=True)
    last_updated = Column(DateTime, nullable=False, default=datetime.now)


class ManagerWorkAllocationFinding(Base):
    """One row per manager (ABM or RBM) -- the Manager Work Allocation
    engine's computed result for that manager, generated from
    ManagerWorkAllocationRecord (see app/manager_work_allocation_service.py
    for ABM, app/manager_work_allocation_rbm_service.py for RBM).
    Full-replacement table, same reasoning as WorkDistributionFinding --
    each engine scopes its own delete-then-rebuild to
    `designation == "ABM"` or `"RBM"` respectively, never touching the
    other engine's rows.

    designation: "ABM" | "RBM".

    total_bms/passed_bms/failed_bms are reused by both engines with a
    per-engine meaning (see each column's own display heading in that
    engine's own FINDINGS_HEADINGS): for ABM, passed/failed means met/missed
    the Minimum Joint Working Days threshold; for RBM, passed/failed means
    covered/not-covered (worked with at least once this month) --
    genuinely the same shape of result (N BMs, some meeting a bar, some
    not), just a different bar per engine, so one column pair serves both
    rather than a duplicate set of columns per engine.

    coverage_percent (RBM only currently; NULL for ABM rows) and reason
    (populated by RBM; NULL for ABM rows, which show their KPI breakdown
    via total_bms/passed_bms/failed_bms alone, per the ABM Findings page's
    own fixed 6-column spec) were added for the RBM Findings page's own
    "Coverage %"/"Reason for flagging" requirements. Nothing here changes
    ABM's own established behavior -- both are nullable and ABM's own
    INSERT never sets them, so they stay NULL on every ABM row exactly as
    before this column existed.

    status: "Pass" | "Flagged" -- for ABM, Flagged if ANY of this manager's
    BMs has an AVERAGE joint-days-per-month, across the current rolling
    six-month window (see ManagerWorkAllocationRecord's own docstring),
    below the Minimum Joint Working Days threshold IN EFFECT AT THE TIME
    OF THIS UPLOAD (see app.manager_work_allocation_parameters_service);
    for RBM, Flagged per that engine's own BM-count-tiered missed-BM
    threshold (see app.manager_work_allocation_parameters_service.get_rbm_flag_tiers),
    evaluated against each BM's joint days SUMMED across that same rolling
    window (RBM's own coverage rule never changed -- see
    app.manager_work_allocation_rbm_service's own module docstring). Both
    are snapshots, not live recompute, same convention as
    WorkDistributionFinding: changing a threshold afterward never
    retroactively re-flags an already-generated finding, only the next
    upload does.

    Always stored on the config/main database (get_config_session())."""

    __tablename__ = "manager_work_allocation_findings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_name = Column(String, nullable=False)
    designation = Column(String, nullable=False, default="ABM")
    division = Column(String, nullable=True)
    total_bms = Column(Integer, nullable=False, default=0)
    passed_bms = Column(Integer, nullable=False, default=0)
    failed_bms = Column(Integer, nullable=False, default=0)
    coverage_percent = Column(Float, nullable=True)
    reason = Column(String, nullable=True)
    status = Column(String, nullable=False, default="Pass")
    last_updated = Column(DateTime, nullable=False, default=datetime.now)


class ManagerWorkAllocationBMDetail(Base):
    """One row per (manager, subordinate) LOGICAL relationship, evaluated
    across the CURRENT rolling six-month window (see
    ManagerWorkAllocationRecord's own docstring) -- the Employee Details
    page's own source for "every BM under this manager"'s AGGREGATE result
    (the per-month breakdown itself is read straight from
    ManagerWorkAllocationRecord, not duplicated here). Full-replacement
    table, rebuilt alongside ManagerWorkAllocationFinding on every upload
    -- each engine scopes its own delete-then-rebuild to
    `manager_designation == "ABM"` or `"RBM"`, never touching the other
    engine's rows.

    joint_days: for ABM, the AVERAGE joint days per month across every
    retained month this pair has a record for (a Float -- "4.17", not
    truncated to a whole number); for RBM, the SUM across those same
    retained months (RBM's own coverage rule never changed -- "covered" if
    total > 0 -- only which months get summed into that total did).

    required_days is a SNAPSHOT of the Minimum Joint Working Days
    threshold in effect at upload time (ABM only -- always 0 for RBM rows,
    which have no day-count threshold, only a covered/not-covered check),
    stored per row rather than looked up live from Settings when this page
    is viewed -- so a later Settings change can never make this table
    disagree with its parent ManagerWorkAllocationFinding row's own
    passed_bms/failed_bms/status, same snapshot reasoning as that Finding's
    own docstring.

    status: ABM rows use "Pass"/"Fail" (average met/missed the day
    threshold); RBM rows use "Yes"/"No" (covered/not covered) -- per each
    engine's own BM_DETAIL_HEADINGS ("Status" for ABM, "Covered" for RBM).

    reason (RBM only currently; NULL for ABM rows, whose per-BM status is
    already self-explanatory via joint_days vs required_days) explains
    why this specific BM is/isn't covered, per the RBM Employee Details
    page's own "Reason" column requirement.

    Always stored on the config/main database (get_config_session())."""

    __tablename__ = "manager_work_allocation_bm_details"

    id = Column(Integer, primary_key=True, autoincrement=True)
    manager_name = Column(String, nullable=False)
    manager_designation = Column(String, nullable=False, default="ABM")
    division = Column(String, nullable=True)
    subordinate_name = Column(String, nullable=False)
    joint_days = Column(Float, nullable=False, default=0.0)
    required_days = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="Pass")
    reason = Column(String, nullable=True)
    last_updated = Column(DateTime, nullable=False, default=datetime.now)


class InventoryEmailNotification(Base):
    """One row per Inventory Automated Email send attempt -- the Inventory
    mirror of EmailNotification (Path Validator) above, read by the
    Automated Emails page's own send log, following the identical
    status vocabulary (Draft | Sent | Failed) plus one Inventory-specific
    addition: "Skipped - No Data", written when a recipient's
    division-filtered report has zero replenishment-required rows right
    now -- deliberately never sent (an empty attachment helps no one) but
    still logged so the run is fully auditable rather than silently
    invisible.

    Unlike EmailNotification, there is no `import_id` to key off of --
    Inventory has no session/workbook-import concept the way Path
    Validator does; `report_type` (currently always "Replenishment")
    identifies WHICH report this attempt covered instead, so this same
    table can log Threshold/CWH report attempts too once those reporting
    frameworks are built (see app/inventory_notification_service.py's
    module docstring for the planned reusable shape) without a schema
    change. `recipient_name`/`recipient_email`/`divisions` are a snapshot
    of the InventoryEmailRecipient row AT SEND TIME (not a foreign key) --
    editing or deleting a recipient later must never rewrite this
    attempt's own history.

    Always stored on the config/main database (get_config_session()),
    same reasoning as InventoryEmailRecipient above."""

    __tablename__ = "inventory_email_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_type = Column(String, nullable=False, default="Replenishment")
    recipient_name = Column(String, nullable=True)
    recipient_email = Column(String, nullable=True)
    divisions = Column(String, nullable=True)
    subject = Column(String, nullable=False)
    body = Column(String, nullable=False)
    row_count = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="Draft")
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    sent_at = Column(DateTime, nullable=True)


class WorkDistributionEmailNotification(Base):
    """One row per Work Distribution automated notification send attempt --
    the Work Distribution mirror of InventoryEmailNotification/
    EmailNotification above, following the identical status vocabulary
    (Draft | Sent | Failed). One row per RECIPIENT per send run (a
    consolidated email covering however many flagged employees routed to
    that recipient across RGD Coverage and/or Manager Work Allocation) --
    never one row per flagged employee, matching the "one email per
    recipient, never one per employee" grouping rule this module's own
    sending logic enforces (see app/work_distribution_notification_service.py).

    `sections` is a comma-separated list of the distinct modules ("RGD
    Coverage", "Manager Work Allocation") this email included content for
    -- descriptive only, not a grouping key (see the employee-first
    redesign in app/work_distribution_notification_service.py: an employee
    flagged under both modules produces one card with two `reasons`
    entries, not two separate cards, so this column can no longer be read
    as "one row per section").
    `employee_count` is the total number of distinct flagged employees
    covered in this one email.

    `recipient_name`/`recipient_email` are a snapshot at send time (read
    from the employee_hierarchy table then, not a foreign key) -- a later
    hierarchy refresh must never rewrite this attempt's own history.

    Always stored on the config/main database (get_config_session()), same
    reasoning as WorkDistributionParameter above -- Work Distribution has no
    Developer Mode concept of its own."""

    __tablename__ = "work_distribution_email_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recipient_name = Column(String, nullable=True)
    recipient_email = Column(String, nullable=True)
    sections = Column(String, nullable=True)
    employee_count = Column(Integer, nullable=False, default=0)
    subject = Column(String, nullable=False)
    body = Column(String, nullable=False)
    status = Column(String, nullable=False, default="Draft")
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    sent_at = Column(DateTime, nullable=True)


class WorkDistributionUploadLog(Base):
    """One row per successfully browsed/connected Work Distribution input
    file -- the Hierarchy Workbook, an RGD Coverage division file, or a
    Manager Work Allocation division file (ABM or RBM) -- feeding the
    Dashboard's own Recent Uploads panel.

    Logged at the point of a successful Browse/parse (or a successful
    hierarchy workbook connection), NOT at Run Analysis time -- so the
    panel reflects what was recently picked up even before Run Analysis is
    clicked, matching "Recent Uploads" in its most literal sense. This is a
    pure activity log: it is never read by any calculation, threshold, or
    finding -- only by the Dashboard's own display.

    `upload_type` is one of "Hierarchy Workbook", "RGD Coverage", "Manager
    Work Allocation (ABM)", "Manager Work Allocation (RBM)". `division` is
    the division name (Onyx/Guardians/Xandra) for the three report types --
    blank for a Hierarchy Workbook connection, which is per-workbook, not
    per-division.

    Always stored on the config/main database (get_config_session()) --
    same reasoning as every other Work Distribution table: this module has
    no Developer Mode concept of its own."""

    __tablename__ = "work_distribution_upload_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_name = Column(String, nullable=False)
    upload_type = Column(String, nullable=False)
    division = Column(String, nullable=True)
    status = Column(String, nullable=False, default="Uploaded")
    uploaded_at = Column(DateTime, nullable=False, default=datetime.now)
