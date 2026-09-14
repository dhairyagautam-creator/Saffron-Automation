"""Structural smoke test for the Inventory Monitoring module shell.

Exercises page construction and navigation cycling. The Thresholds page's
on_show() now queries the real database (app/threshold_service.py), so
this test points app.config.DATABASE_PATH at an isolated scratch file
before touching any DB-backed module, rather than reading/writing the
real project database.
"""

import sys
from pathlib import Path

import pytest

import app.config as config

_SCRATCH_DIR = Path(r"C:\Users\Hp\AppData\Local\Temp\claude\test_inventory_module_shell_db")
_SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
config.DATABASE_PATH = _SCRATCH_DIR / "saffron_validator.db"
for _p in _SCRATCH_DIR.glob("*.db"):
    _p.unlink(missing_ok=True)

import customtkinter as ctk

from database.connection import init_db
from ui.inventory_module import BASE_PAGES, InventoryModule

init_db()


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="root.update() blocks forever on GitHub's macos-latest CI runner -- "
           "confirmed via pytest-timeout thread dump (tkinter/__init__.py:1373, "
           "self.tk.call('update')): no WindowServer/Aqua session for Tk to reach "
           "there, unlike a real interactive Mac. Runs normally on Windows/Linux "
           "CI and locally on an actual Mac.",
)
def test_inventory_module_builds_and_cycles_pages() -> None:
    root = ctk.CTk()
    root.withdraw()
    try:
        module = InventoryModule(root, on_home=lambda: None)
        root.update()

        assert set(module.pages.keys()) == set(BASE_PAGES)
        assert module.active_page == "Dashboard"

        for name in BASE_PAGES:
            module.show_page(name)
            root.update()
            assert module.active_page == name

        module.on_show()
        root.update()
        assert module.active_page == "Settings"
    finally:
        root.destroy()
