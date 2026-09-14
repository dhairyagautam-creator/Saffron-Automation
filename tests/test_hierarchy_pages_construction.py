"""Real Tk construction test for the three module-specific hierarchy pages
(ui/organization_data_page.py, ui/work_distribution_email_center_page.py,
ui/review_hierarchy_page.py) after the Employee Hierarchy 3-way split --
each must construct cleanly and each's HierarchyTableSection must be wired
to that page's OWN module_key, not a shared/default one.

Uses tests/db_isolation.py's shared isolate_database() helper -- this file
also touches get_data_engine()-based code via HierarchyTableSection.
load_from_db(), so both _Session and _engine must be patched together (see
that module's docstring for why -- patching only one is exactly what let
an earlier version of test_hierarchy_module_split.py write fake rows into
this project's real production database).
"""

import pytest

from tests.db_isolation import isolate_database


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    isolate_database(monkeypatch)


@pytest.fixture(scope="module")
def _tk_root(_shared_tk_root):
    # See tests/conftest.py's _shared_tk_root docstring -- this reuses the
    # one session-wide root instead of creating/destroying its own.
    return _shared_tk_root


def test_organization_data_page_uses_path_validator_module_key(_tk_root):
    from ui.organization_data_page import MODULE_KEY, OrganizationDataPage

    assert MODULE_KEY == "employee_module"
    page = OrganizationDataPage(_tk_root)
    try:
        _tk_root.update()
        assert page.hierarchy_section._module_key == "employee_module"
    finally:
        page.destroy()


def test_work_distribution_email_center_page_uses_work_distribution_module_key(_tk_root):
    from ui.work_distribution_email_center_page import MODULE_KEY, WorkDistributionEmailCenterPage

    assert MODULE_KEY == "work_distribution"
    page = WorkDistributionEmailCenterPage(_tk_root)
    try:
        _tk_root.update()
        assert page.hierarchy_section._module_key == "work_distribution"
    finally:
        page.destroy()


def test_review_hierarchy_page_uses_review_system_module_key(_tk_root):
    from ui.review_hierarchy_page import MODULE_KEY, ReviewHierarchyPage

    assert MODULE_KEY == "review_system"
    page = ReviewHierarchyPage(_tk_root)
    try:
        _tk_root.update()
        assert page.hierarchy_section._module_key == "review_system"
    finally:
        page.destroy()


def test_all_three_module_keys_are_distinct():
    from ui.organization_data_page import MODULE_KEY as PV_KEY
    from ui.review_hierarchy_page import MODULE_KEY as RS_KEY
    from ui.work_distribution_email_center_page import MODULE_KEY as WD_KEY

    assert len({PV_KEY, WD_KEY, RS_KEY}) == 3
