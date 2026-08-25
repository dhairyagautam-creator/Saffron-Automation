"""Single source of truth for the application's version, shown on the About
page (ui/about_page.py) and referenced by the Inno Setup installer script
(installer/saffron_validator.iss) — bump both together for a new release,
see README.md's "Releasing a new version" section.

CHANNEL controls which GitHub release feed app/updater.py checks:
"production" polls GitHub's /releases/latest (stable releases only, by
GitHub's own API contract); "development" polls the /releases list and
picks the latest entry marked as a pre-release. The two channels never see
each other's builds -- see app/updater.py's module docstring.

For a new Development build: bump APP_VERSION only (e.g. "1.3.1-dev.0.2"),
leave CHANNEL as "development". For a Production release: set APP_VERSION
to the plain version (no "-dev.X.Y" suffix) and CHANNEL to "production".
"""

APP_VERSION = "2.3.2"
CHANNEL = "production"  # "development" | "production"
BUILD_DATE = "2026-08-25"
DEVELOPER = "Dhairya Gautam"
COMPANY = "Saffron Formulations"
DESCRIPTION = "Internal Enterprise Automation Platform (Path Validator, Inventory Monitoring, Payment Analytics)"
