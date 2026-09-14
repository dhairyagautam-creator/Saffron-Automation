"""Shared pytest fixtures.

_shared_tk_root: ONE ctk.CTk() root for the whole test session, reused by
every test file that needs to construct real Tk widgets, instead of each
file creating and destroying its own. Repeatedly creating a fresh Tk root
after a previous one in the same process was destroyed is fragile -- on
Windows this surfaces as an intermittent TclError ("invalid command name
tcl_findLibrary" / missing ttk.tcl); on GitHub's macos-latest CI runner it
surfaces as root.update() hanging indefinitely (confirmed via
pytest-timeout's thread dump). Both are the same underlying Tcl/Tk
global-interpreter-state issue, just failing differently per platform.
tests/test_inventory_uploads_page_panel.py's docstring already documented
this for its own two tests, sharing one root across them; this fixture
generalizes that same fix across the whole session instead of per-file.
"""

import customtkinter as ctk
import pytest


@pytest.fixture(scope="session")
def _shared_tk_root():
    root = ctk.CTk()
    root.withdraw()
    yield root
    root.destroy()
