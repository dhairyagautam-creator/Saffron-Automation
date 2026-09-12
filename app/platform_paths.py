"""Minimal platform-specific path resolution.

Currently handles only what's needed to stop bundling .env inside the
frozen macOS .app (see "Saffron Automation-mac.spec" and build.yml's
macos job): a signed, notarizable .app bundle is meant to be read-only,
so Supabase credentials baked into it at build time can never be rotated
without a full rebuild, unlike a plain file sitting in the user's own
data directory.

Windows/source behavior is untouched -- app/supabase_client.py's .env
resolution still lands exactly where it always has (next to the repo
root when run from source, or next to the frozen build's _internal/ dir
on Windows, still bundled there by "Saffron Automation.spec").

This is intentionally narrow. Folding app/config.py's existing Windows
DATA_DIR logic (write-probe fallback, legacy-db migration,
SAFFRON_DATA_DIR override) and this module into one shared per-platform
path module is the larger, separate macOS-port pass -- not this one.
"""

import sys
from pathlib import Path


def macos_data_dir() -> Path:
    """~/Library/Application Support/Saffron Automation -- the standard
    per-user, always-writable location for a signed macOS app's data,
    analogous to Windows' %LOCALAPPDATA% (see app/config.py). Created if
    missing."""
    data_dir = Path.home() / "Library" / "Application Support" / "Saffron Automation"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def env_path() -> Path:
    """Where the app reads its .env at runtime.

    Frozen macOS build only: the per-user Application Support directory,
    never inside the read-only, code-signed .app bundle.

    Everything else (source runs on any OS, and the frozen Windows
    build): unchanged from app/supabase_client.py's original formula --
    next to the repo root when run from source, or next to the frozen
    Windows build's _internal/ dir (still bundled there by
    "Saffron Automation.spec"; not touched by this pass)."""
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        return macos_data_dir() / ".env"
    return Path(__file__).resolve().parent.parent / ".env"
