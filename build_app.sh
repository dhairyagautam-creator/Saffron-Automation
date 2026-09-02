#!/usr/bin/env bash
# Builds Saffron Automation into a macOS .app bundle and packages it as a
# signed, notarized .dmg. The macOS counterpart to build_exe.ps1 -- kept
# separate so the two platform builds never interfere. Run this ON macOS
# (PyInstaller cannot cross-compile a Mac app from Windows).
#
# Usage:  ./build_app.sh
#
# Signing (required for the app to launch at all on Apple Silicon, and for
# it to survive being re-run on the SAME machine after a rebuild):
#   SIGN_IDENTITY="Developer ID Application: Your Org (TEAMID)"
# Without SIGN_IDENTITY the script ad-hoc signs instead (SIGN_IDENTITY="-"),
# which lets you smoke-test the .app locally but will NOT pass Gatekeeper
# once the app is downloaded/transferred elsewhere -- see README below.
#
# Notarization (required for Gatekeeper to accept the app on ANY OTHER
# Mac -- this is what a "not opened ... could not verify" dialog means: the
# app was unsigned or unnotarized when it picked up the quarantine flag from
# being downloaded/transferred). Optional: skipped with a warning if unset.
# Either an API key:
#   APPLE_API_KEY_ID, APPLE_API_ISSUER, APPLE_API_KEY_PATH (path to the .p8)
# or an Apple ID + app-specific password (generate one at appleid.apple.com):
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_SPECIFIC_PASSWORD
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
SIGN_IDENTITY="${SIGN_IDENTITY:--}"
ENTITLEMENTS="entitlements.plist"

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

# 4b. Sign the whole bundle (--deep: PyInstaller's own EXE()-level signing
# only covers the main executable, not the dozens of bundled .dylibs/.so
# files -- notarization requires EVERY Mach-O inside to be signed). Hardened
# runtime (--options runtime) is mandatory for notarization; entitlements.plist
# relaxes the two hardened-runtime restrictions CPython/Tcl-Tk need (see that
# file). codesign must run AFTER PyInstaller, not via the spec's own
# codesign_identity= (which can't pass --deep or --options runtime).
echo ">> Signing $APP_PATH ($SIGN_IDENTITY)"
codesign --deep --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" --sign "$SIGN_IDENTITY" "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
if [ "$SIGN_IDENTITY" = "-" ]; then
  echo "!! Ad-hoc signed (no SIGN_IDENTITY set) -- this build will FAIL Gatekeeper"
  echo "   on any other Mac. Set SIGN_IDENTITY to a 'Developer ID Application'"
  echo "   certificate for a distributable build."
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

# 7. Sign the dmg itself (Gatekeeper checks the container too, not just the
# .app inside it).
echo ">> Signing $DMG_PATH"
codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG_PATH"

# 8. Notarize + staple, if Apple credentials were provided. This is the step
# that actually satisfies Gatekeeper on a machine that isn't this one --
# signing alone (even with a real Developer ID) is not enough once the file
# has a quarantine attribute (i.e. was downloaded/AirDropped/emailed rather
# than built locally). notarytool blocks until Apple's servers finish
# processing (usually under a few minutes).
if [ -n "${APPLE_API_KEY_ID:-}" ] && [ -n "${APPLE_API_ISSUER:-}" ] && [ -n "${APPLE_API_KEY_PATH:-}" ]; then
  echo ">> Notarizing (API key)"
  xcrun notarytool submit "$DMG_PATH" --wait \
    --key "$APPLE_API_KEY_PATH" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER"
  xcrun stapler staple "$DMG_PATH"
elif [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" ]; then
  echo ">> Notarizing (Apple ID)"
  xcrun notarytool submit "$DMG_PATH" --wait \
    --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_SPECIFIC_PASSWORD"
  xcrun stapler staple "$DMG_PATH"
else
  echo "!! No Apple notarization credentials set (APPLE_API_KEY_ID/APPLE_API_ISSUER/APPLE_API_KEY_PATH"
  echo "   or APPLE_ID/APPLE_TEAM_ID/APPLE_APP_SPECIFIC_PASSWORD) -- skipping notarization."
  echo "   This dmg will still fail Gatekeeper on another Mac."
fi

echo ">> Done."
echo "   App: $APP_PATH"
echo "   DMG: $DMG_PATH"
echo ""
echo "   Verify before distributing:"
echo "     codesign --verify --deep --strict \"$APP_PATH\""
echo "     spctl -a -vvv --type execute \"$APP_PATH\""
echo "     spctl -a -vvv --type open --context context:primary-signature \"$DMG_PATH\""
echo "     xcrun stapler validate \"$DMG_PATH\""
