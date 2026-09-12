# -*- mode: python ; coding: utf-8 -*-
#
# macOS build spec -- produces "Saffron Automation.app" (a .app bundle),
# packaged into a .dmg by build_app.sh. Deliberately SEPARATE from the
# Windows spec ("Saffron Automation.spec") so the Windows build stays byte-
# for-byte unchanged; only two things differ from Windows here:
#   * upx=False -- UPX corrupts Mach-O binaries / signing on macOS, and is
#     usually absent on the runner anyway.
#   * a .icns icon + a BUNDLE() stanza to emit a real .app.
# Everything else (entry point, hidden imports) is IDENTICAL to the Windows
# spec on purpose. KEEP _SUPABASE_DEPENDENCY_PACKAGES in sync with
# "Saffron Automation.spec" -- if you add a dependency there, add it here.
#
# One deliberate difference in `datas`: unlike the Windows spec, this one
# does NOT bundle .env into the app. A signed/notarized .app is meant to be
# read-only, so baked-in Supabase credentials could never be rotated without
# a full rebuild. app/platform_paths.py's env_path() instead points a frozen
# macOS build at ~/Library/Application Support/Saffron Automation/.env at
# runtime -- see that module's docstring.

from PyInstaller.utils.hooks import collect_submodules

from app.version import APP_VERSION

# Same rationale as the Windows spec: these packages use dynamic/conditional
# imports PyInstaller's static analysis misses, so collect them explicitly.
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
# macOS-specific keyring backend (Keychain via the Security framework) -- the
# generic collect_submodules above usually catches it, but name it explicitly
# so auth-token storage can never silently fall back to a plaintext backend.
hidden_imports += ["keyring.backends.macOS", "keyring.backends.macOS.api"]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
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
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Saffron Automation',
)
app = BUNDLE(
    coll,
    name='Saffron Automation.app',
    icon='assets/icon.icns',
    bundle_identifier='com.saffronformulations.saffronautomation',
    version=APP_VERSION,
    info_plist={
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'NSHighResolutionCapable': True,
        'LSApplicationCategoryType': 'public.app-category.business',
    },
)
