# Architecture

Generated 2026-08-05 from a direct read of the codebase (entry point, connection layer, and every
service-layer docstring cited below). See `PROJECT_CONTEXT.md` for which folder this describes.

## Stack

- **UI:** Python 3.12 + `customtkinter` (a themed layer over Tkinter) — a single-process Windows desktop
  app, not a web app.
- **Local storage:** SQLite via SQLAlchemy (`database/`).
- **Cloud:** Supabase (Postgres) — auth, cross-machine config/data sync, storage buckets.
- **Logging:** `loguru` (`app/logging_config.py`).
- **Packaging:** PyInstaller `--onedir` (`Saffron Automation.spec`, `build_exe.ps1`) + Inno Setup installer
  (`installer/saffron_validator.iss`).
- **Auto-update:** GitHub Releases, channel-aware (`app/updater.py`).

## Entry point (`main.py`)

1. `configure_logging()` — guarded so a logging failure never blocks startup.
2. Resolve a writable data directory (`app/config.py`); fatal dialog + exit if none exists (a windowed
   PyInstaller exe has no console, so this is the only way the user ever finds out).
3. `init_db()` + `run_startup_migrations()`.
4. `ensure_defaults()` / `ensure_payment_parameter_defaults()` / `ensure_inventory_parameter_defaults()` /
   `ensure_work_distribution_parameter_defaults()` / `ensure_manager_work_allocation_parameter_defaults()` /
   `ensure_flag_defaults()` — every module's own Settings defaults, seeded idempotently on every launch.
5. `MainWindow().mainloop()`.

## UI layer

`ui/main_window.py` is a **screen registry**: every module is built once at startup into
`self.screens[name]`, and `show_screen(name)` just calls `tkraise()` — no window teardown/rebuild, no
routing framework. `Login` is just another screen in the same registry, not a separate window.

Each module below Home is its own "shell": a branded sidebar + its own set of internal pages, following
the same shape (`ui/path_validator_module.py` was the original; `ui/inventory_module.py`,
`ui/payment_analytics_module.py`, `ui/work_distribution_module.py` all mirror it). Path Validator alone
also owns the User Mode / Developer Mode chrome (amber banner, badge, conditional Developer page) — that
concept is Path-Validator-specific, not app-wide.

Access to a module screen is centrally checked in `MainWindow.show_screen()` via
`_MODULE_PERMISSION_CHECKS`, not by hiding the entry point — a card a role doesn't grant is never even
built (see `ui/home_page.py`'s own docstring: "not just disabled, so there's nothing in the DOM/widget
tree to find"). **Work Distribution is currently missing from this dict** — see `PROJECT_STATUS.md`.

## Service layer

One `app/*_service.py` (or `*_parser.py`/`*_parameters_service.py`) file per concern, called directly by
`ui/` pages — there is no separate API/HTTP layer since this is a single process. Business logic, DB
reads/writes, and validation all live in this layer; `ui/` files are rendering + event wiring only.

## Data layer

`database/connection.py` — **two-engine design** for complete User Mode / Developer Mode isolation
(`app/mode_state.py`):
- **Config engine** — always the main DB file. Configuration tables (`app_settings`, `rule_parameters`,
  `feature_flags`, every module's own parameters table) plus the User environment's own data. Config
  services always use `get_config_session()`.
- **Data engine** — mode-dependent. User Mode: same file as config (nothing changes for existing
  installs). Developer Mode: a fully separate file (`saffron_validator_dev.db`) — every upload, cache,
  finding, and session-state row created in Developer Mode is physically isolated from User Mode. Only an
  explicit Publish copies configuration across (never data).
- Call-site rule of thumb: configuration → `get_config_session()`/`get_config_engine()`; everything else
  (uploads, findings, caches, emails, session) → `get_session()`/`get_data_session()`/`get_data_engine()`,
  which follow the current mode automatically.

`database/models.py` — SQLAlchemy models for every fixed-schema table (see `DATABASE_SCHEMA.md`).
`raw_visits` (Path Validator's own uploaded-report table) is deliberately **not** modeled here — its
columns come straight from whatever the uploaded Excel contains, created dynamically by pandas (see
`database/import_service.py`).

`database/migrations.py` — startup-time migrations (additive schema changes applied idempotently on
every launch, not a separate migration-runner CLI).

## Auth & RBAC

- `app/auth_service.py` — Supabase Auth (sign in/out, session persistence via the OS credential store —
  `keyring` on Windows), centralized so UI code never touches the Supabase client or gotrue exceptions
  directly.
- `app/rbac_service.py` — after sign-in, loads the user's profile/role from Supabase's `profiles`/`roles`
  tables into `app/rbac_state.py` (in-memory, mirrors `app/mode_state.py`'s pattern). This module only
  *loads and logs* — it does not itself enforce anything, except account-level pass/fail gates (missing
  profile, or an explicitly disabled account) which block access entirely, not partially.
- `app/permissions.py` — the single source of truth for "can this user see/use X". Every
  `can_access_*_module()` check funnels through here; fails closed (no role loaded → every check is
  `False`). UI code never re-implements a permission check ad hoc.

## Cloud sync

- `app/supabase_client.py` — shared client + connectivity status.
- `app/sync_service.py` — **generic** per-module JSON config sync: pushes/pulls a module's entire
  configuration as one blob against Supabase's `module_configurations` table
  (`supabase/migrations/0008_module_configurations.sql`). Knows nothing about any specific module's data
  shape — any future module reuses it by calling with its own `module_key` and its own local read/write
  functions.
- Per-module data-sync services (each syncing that module's own tables against its own
  `supabase/migrations/00NN_*.sql` schema): `import_sync_service.py`, `findings_sync_service.py`,
  `email_sync_service.py`, `organization_data_sync_service.py` (Path Validator); `inventory_sync_service.py`
  (Inventory); `payment_sync_service.py` (Payment Analytics).
- **Work Distribution / Manager Work Allocation currently have no cloud-sync counterpart at all** — no
  `supabase/migrations` entry, no `*_sync_service.py`. Confirmed via a migrations-folder scan (only 19
  migrations exist, `0001`–`0019`, none referencing Work Distribution or Manager Work Allocation) — this
  module is fully local-only for now, matching its own service docstrings ("does not... sync to the
  cloud").
- Last-Modified-Wins is the app-wide conflict-resolution rule for anything with both a local
  `updated_at` and a cloud `updated_at` (see `WorkbookConnection`'s own docstring in `database/models.py`
  for the canonical example).

## Developer Mode

`app/mode_state.py` (User vs. Developer environment) + `app/feature_flags_service.py` (per-environment
on/off toggles) — an experimental feature ships flagged on in `developer` and off in `user`, so production
never runs it until a developer explicitly Publishes (`app/publish_service.py`, config only, never data).
Currently Path-Validator-specific chrome; not a concept the other modules opt into.

## Auto-update

`app/updater.py` — checks GitHub Releases, channel-aware via `app/version.py`'s `CHANNEL`:
`"production"` polls `/releases/latest` (GitHub's own latest-non-prerelease endpoint — structurally
cannot return a dev build); `"development"` polls the full `/releases` list and keeps only entries GitHub
itself marked pre-release. The two channels can never cross-contaminate by construction, not just by
convention.

## Packaging

`Saffron Automation.spec` (PyInstaller, `--onedir` — produces an exe + `_internal/` folder of extracted
dependencies) + `build_exe.ps1`. Installer via Inno Setup (`installer/saffron_validator.iss`). See
`UPDATER_README.md` for the release workflow. **This project has never been packaged yet** — no
`dist/`/`build/` output exists here (see `PROJECT_STATUS.md`).

## Directory map

```
app/            Service layer — one file per concern, called by ui/
ui/             CustomTkinter pages/modules — rendering + event wiring only
database/       SQLAlchemy models, connection (two-engine), startup migrations
rules/          Path Validator's own rule engines (e.g. same_location.py)
supabase/migrations/   Cloud (Postgres) schema, sequential .sql files
installer/      Inno Setup installer script
tests/          Test suite
assets/         Icons, logo, bundled GeoJSON, etc.
cloud_cache/    Local cache of pulled cloud data (e.g. organization_data)
```
