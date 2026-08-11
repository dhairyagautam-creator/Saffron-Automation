# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

# Version 2.0's Supabase integration (app/supabase_client.py, auth_service.py,
# rbac_service.py, user_management_service.py, sync_service.py) pulls in
# httpx/supabase/postgrest/gotrue and their dependency tree. These packages
# use enough dynamic/conditional imports (different HTTP backends, plugin-
# style credential-store backends, etc.) that PyInstaller's static analysis
# misses real submodules unless explicitly collected here -- without this,
# `python main.py` from source works fine (the venv has everything
# installed), but the frozen .exe crashes on first launch with
# ModuleNotFoundError, since PyInstaller never bundled the missing module.
_SUPABASE_DEPENDENCY_PACKAGES = (
    "httpx", "httpcore", "h11", "h2", "hpack", "hyperframe", "anyio",
    "certifi", "idna",
    "supabase", "postgrest", "supabase_auth", "storage3",
    "supabase_functions", "realtime",
    "pydantic", "pydantic_core", "annotated_types", "typing_inspection",
    "jwt", "cryptography", "cffi",
    "keyring", "jaraco.classes", "jaraco.context", "jaraco.functools",
    "dotenv",
    "websockets",
    "strenum", "deprecation", "yarl", "multidict", "propcache",
)
hidden_imports = []
for _package in _SUPABASE_DEPENDENCY_PACKAGES:
    hidden_imports.extend(collect_submodules(_package))


# .env holds SUPABASE_URL/SUPABASE_ANON_KEY (see app/supabase_client.py).
# It's gitignored (correctly -- it's not meant to be committed), but that
# also meant it was never bundled into a packaged build at all: any
# machine running only the installed .exe (no source checkout sitting next
# to it) had literally no Supabase configuration, which surfaced as
# authentication silently never completing. Bundled here at the same
# relative path app/supabase_client.py's `Path(__file__).resolve().parent.
# parent / ".env"` resolves to for a frozen module (PyInstaller synthesizes
# `__file__` as `<install_dir>\_internal\app\supabase_client.py` even
# though the actual bytecode lives in the PYZ archive, so that parent.parent
# is `_internal` itself -- hence destination "." here, not "assets").
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets'), ('.env', '.')],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Saffron Automation',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Saffron Automation',
)
