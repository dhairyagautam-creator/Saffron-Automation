#!/usr/bin/env bash
# Builds Saffron Automation into a macOS .app bundle and packages it as a
# .dmg. The macOS counterpart to build_exe.ps1 -- kept separate so the two
# platform builds never interfere. Run this ON macOS (PyInstaller cannot
# cross-compile a Mac app from Windows).
#
# Usage:  ./build_app.sh
#
# Output:
#   dist/Saffron Automation.app                          (the app bundle)
#   dist/release/Saffron-Automation-macOS-<version>.dmg  (the installer)
#
# User data (database, logs, credentials) is NOT bundled -- it lives in a
# per-user writable location at runtime (see app/config.py), same as Windows.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV_DIR=".venv-mac"
SPEC="Saffron Automation-mac.spec"
APP_NAME="Saffron Automation"

# 1. Environment: reuse an existing .venv-mac, else create one. Kept distinct
#    from the Windows .venv so a shared checkout doesn't clobber either.
if [ ! -d "$VENV_DIR" ]; then
  echo ">> Creating virtualenv $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# 2. Dependencies (pyinstaller is listed in requirements.txt already).
echo ">> Installing requirements"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

# 3. PyInstaller needs a .env to bundle (datas=[('.env', '.')]). Create an
#    empty one if missing so the build never fails on a fresh checkout; real
#    Supabase keys are injected by CI or a developer's local .env.
if [ ! -f ".env" ]; then
  echo ">> No .env found -- creating an empty placeholder for the build"
  : > .env
fi

# 4. Build the .app.
echo ">> Building $APP_NAME.app"
python -m PyInstaller --noconfirm --clean "$SPEC"

APP_PATH="dist/$APP_NAME.app"
if [ ! -d "$APP_PATH" ]; then
  echo "!! Build failed: $APP_PATH not found" >&2
  exit 1
fi

# 5. Version for artifact naming (single source of truth: app/version.py).
VERSION="$(python -c 'from app.version import APP_VERSION; print(APP_VERSION)')"
RELEASE_DIR="dist/release"
mkdir -p "$RELEASE_DIR"
DMG_PATH="$RELEASE_DIR/Saffron-Automation-macOS-$VERSION.dmg"

# 6. Package into a .dmg using native macOS tooling (hdiutil). A plain
#    drag-to-Applications layout is enough; skip create-dmg to avoid a
#    non-stdlib dependency (ponytail: hdiutil suffices, add create-dmg only
#    if a fancy background/window layout is ever required).
echo ">> Packaging $DMG_PATH"
rm -f "$DMG_PATH"
STAGING="$(mktemp -d)"
cp -R "$APP_PATH" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING" -ov -format UDZO "$DMG_PATH"
rm -rf "$STAGING"

echo ">> Done."
echo "   App: $APP_PATH"
echo "   DMG: $DMG_PATH"
