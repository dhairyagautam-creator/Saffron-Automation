"""The single source of truth for every top-level, permission-gated module
in the application -- screen name, permission key, display text, and icon.

Before this file existed, the same module list was hand-maintained in
THREE separate places (app/permissions.py's old per-module boolean
columns/functions, ui/main_window.py's _MODULE_PERMISSION_CHECKS, and
ui/home_page.py's _module_specs) -- already silently out of sync with each
other (Work Distribution was in Home's list but missing from the other
two, so it was accessible to every signed-in user regardless of role).
Every one of those now builds itself FROM the tuple below instead. Adding
a new module means adding one ModuleDef here; Home Screen, the
screen-registry permission gate (ui/main_window.py), and User Management's
module checklist (ui/user_dialogs.py) all pick it up automatically -- no
other file needs to change, and nothing needs re-registering.

`key` is the permission key stored in Supabase's user_module_permissions
table (see app/permissions.py, app/rbac_service.py, and
supabase/migrations/0020_module_based_permissions.sql) -- stable once a
real grant references it; do not rename an existing module's key without
also migrating existing user_module_permissions rows. `screen_name` must
match exactly what ui/main_window.py's MainWindow.screens registry uses
for that module.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleDef:
    key: str
    screen_name: str
    title: str
    description: str
    icon_name: str


MODULES: tuple[ModuleDef, ...] = (
    ModuleDef(
        key="employee_module",
        screen_name="Path Validator",
        title="Path Validator",
        description=(
            "Detect field-force GPS anomalies from daily call reports, review findings, "
            "and manage manager notifications."
        ),
        icon_name="Operations",
    ),
    ModuleDef(
        key="inventory_module",
        screen_name="Inventory Monitoring",
        title="Inventory Monitoring",
        description=(
            "Upload inventory and sales reports, track replenishment thresholds, "
            "and monitor stock status across every branch."
        ),
        icon_name="Inventory Monitoring",
    ),
    ModuleDef(
        key="payments_module",
        screen_name="Payment Analytics",
        title="Payment Analytics",
        description=(
            "Upload customer payment reports, track payment behavior, and monitor "
            "customer risk across every account."
        ),
        icon_name="Payment Analytics",
    ),
    ModuleDef(
        key="work_distribution",
        screen_name="Work Distribution",
        title="Work Distribution",
        description=(
            "Monitor monthly doctor coverage for BMs and ABMs — upload reports, "
            "review findings, and track KPI compliance."
        ),
        icon_name="Work Distribution",
    ),
    ModuleDef(
        key="user_management",
        screen_name="User Management",
        title="User Management",
        description="Manage user accounts and module permissions.",
        icon_name="Settings",
    ),
    ModuleDef(
        key="review_system",
        screen_name="Review System",
        title="Review System",
        description="Upload, preview, and review files against your organization's hierarchy.",
        icon_name="Review System",
    ),
)

_BY_KEY = {module.key: module for module in MODULES}
_BY_SCREEN_NAME = {module.screen_name: module for module in MODULES}


def all_modules() -> tuple[ModuleDef, ...]:
    return MODULES


def get_by_key(key: str) -> ModuleDef | None:
    return _BY_KEY.get(key)


def get_by_screen_name(screen_name: str) -> ModuleDef | None:
    return _BY_SCREEN_NAME.get(screen_name)
