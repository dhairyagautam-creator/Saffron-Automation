# Saffron Automation Auto-Update System

The Version 2.0 auto-update system allows the application to automatically check for new versions, notify users, download updates, and install them with minimal user interaction.

## Architecture Overview

### Components

1. **app/updater.py** — Core update logic
   - `check_for_updates()`: Fetches latest release from GitHub API
   - `download_installer()`: Downloads the installer to a temp location
   - `perform_update()`: Coordinates download + installation
   - `_get_latest_release()`: Queries GitHub without authentication (rate-limited but sufficient for typical usage)

2. **ui/update_notification_dialog.py** — User-facing update prompt
   - Shows current vs. new version
   - Options: "Install Now", "Later", "View Release Notes"
   - Opens GitHub release page in browser

3. **ui/main_window.py** — Update integration
   - Checks for updates in background thread after UI loads
   - Shows notification if update available
   - Handles user's choice to install or defer

### Data Preservation

User data is automatically preserved because:
- Application files: `%LOCALAPPDATA%\Programs\Saffron Automation`
- User data (database, settings, logs): `%LOCALAPPDATA%\Saffron Validator`
- The Inno Setup installer recognizes upgrades and only replaces application files
- Database, logs, and settings are never touched

### Version Management

The version is defined in a single location:
- **app/version.py** — `APP_VERSION = "1.0.0"`
  - Referenced by the UI (About page, window title)
  - Compared against GitHub releases
  - Must be bumped for each release

### Release Channels (Version 2.0+)

`app/version.py` also defines `CHANNEL`, which controls which GitHub feed
the running build checks:

| CHANNEL | Checks | Version format | GitHub release must be |
|---|---|---|---|
| `"production"` | `/releases/latest` (GitHub's own latest-stable endpoint) | `1.3.1` | a normal release (**not** marked pre-release) |
| `"development"` | the full `/releases` list, filtered to entries marked pre-release | `1.3.1-dev.0.1`, `.0.2`, ... | published with the **"Set as a pre-release"** checkbox checked |

The two channels are structurally incapable of seeing each other's builds
-- a Development build never even asks the endpoint that could return a
stable release, and vice versa. See `app/updater.py`'s module docstring
for the full explanation.

For a **new Development build**: bump only `APP_VERSION` (e.g.
`"1.3.1-dev.0.2"`) in both `app/version.py` and
`installer/saffron_validator.iss`'s `MyAppVersion` -- leave `CHANNEL` /
`MyChannel` as `"development"` in both files. Everything else (install
directory, Add/Remove Programs entry, App ID) is already kept separate
from Production automatically.

For a **Production release**: set `APP_VERSION` to the plain version (no
`-dev.X.Y` suffix) and `CHANNEL` to `"production"` in `app/version.py`,
and `MyChannel` to `"production"` in the `.iss` file.

## Workflow: Creating and Publishing an Update

### Step 1: Develop and Test in v2.0 Development

Work entirely in:
```
C:\Users\Hp\OneDrive\Desktop\Saffron Automation v2.0 - Development
```

Make all changes, test thoroughly, verify everything works.

### Step 2: Bump Version

When ready to release:

1. Edit `app/version.py`:
   ```python
   APP_VERSION = "1.1.0"  # bump from "1.0.0"
   BUILD_DATE = "2026-07-21"  # update to today
   ```

2. Edit `installer/saffron_validator.iss`:
   ```
   #define MyAppVersion "1.1.0"  # match app/version.py
   ```

### Step 3: Build Release Artifacts

```powershell
# Close any running instance of the app first

# Build the executable
.\build_exe.ps1

# Build the installer
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\saffron_validator.iss
```

Outputs:
- `dist\Saffron Automation\Saffron Automation.exe` (executable)
- `installer_output\Saffron Automation Setup v1.1.0.exe` (installer)

### Step 4: Create GitHub Release

1. Go to https://github.com/dhairyagautam-creator/Saffron-Automation/releases
2. Click "Create a new release"
3. **Tag version**: `v1.1.0` (note the `v` prefix) -- for a Development build, use the full
   dev version, e.g. `v1.3.1-dev.0.1`
4. **Release title**: `Saffron Automation v1.1.0`
5. **Description**: Add change notes (fixes, features, improvements)
   ```markdown
   ## Changes in v1.1.0
   - Fixed issue with path validation
   - Improved performance in inventory module
   - Updated UI styling
   ```
6. **For a Development build only**: check **"Set as a pre-release"** near the Publish button.
   This is the one step the whole channel system depends on -- `app/updater.py`'s Development
   channel only ever looks at releases GitHub has marked this way, and a Production build's
   `/releases/latest` check structurally cannot see a pre-release at all. Leave this unchecked
   for a normal Production release.
6. **Attach the installer**:
   - Drag and drop: `installer_output\Saffron Automation Setup v1.1.0.exe`
   - Or use "Attach binaries" button
7. Click "Publish release"

### Step 5: Verify Auto-Update Detection

The next time the app starts:
1. It queries GitHub for the latest release
2. Compares `v1.1.0` from GitHub against `1.0.0` in app/version.py
3. Shows update notification if newer version is available
4. User can install immediately or defer

## Testing the Auto-Update System

### Test 1: Version Check (Without Releasing)

Temporarily modify `app/version.py` to test:
```python
APP_VERSION = "0.9.0"  # Pretend current version is older
```

Run the app → should detect "newer" version in GitHub and show notification.

**Restore the correct version afterward.**

### Test 2: Download and Installation Flow

When you see the update notification:
1. Click "Install Now"
2. Monitor logs: `logs/Saffron Automation*.log`
3. You should see:
   ```
   Checking for updates (current version: 0.9.0)...
   Update available: 0.9.0 → 1.1.0
   Downloading installer from https://github.com/...
   Installer downloaded to C:\Users\Hp\AppData\Local\Temp\...
   Launching installer: C:\Users\Hp\AppData\Local\Temp\...
   ```
4. Windows installer should appear
5. Proceed with installation (select upgrade option)
6. Restart the app
7. Verify app is at new version and all data is intact

### Test 3: Network Failure Handling

Simulate network outage:
1. Unplug network (or use airplane mode)
2. Start the app
3. Should handle gracefully without crashing
4. Check logs for warning message about GitHub unreachable

## Implementation Details

### GitHub API (Unauthenticated)

```python
# Endpoint: GET https://api.github.com/repos/dhairyagautam-creator/Saffron-Automation/releases/latest
# Rate limit: ~60 calls/hour (unauthenticated)
# Response: Latest release JSON with asset URLs
```

The app extracts:
- `tag_name` → version (e.g., "v1.1.0")
- `browser_download_url` → installer download link
- `html_url` → link to release notes

### Installer Behavior

The Inno Setup installer (built in previous step):
1. Detects if app is already installed
2. Offers upgrade option (preserves user choices)
3. Only replaces application files
4. Leaves user data in `%LOCALAPPDATA%\Saffron Validator` untouched
5. Exits cleanly; user can restart app manually

### Non-Blocking Design

- Update check runs on background thread
- Doesn't block UI startup
- User can work while installer downloads/prepares
- No interruption to workflow

## Troubleshooting

### "Could not reach GitHub to check for updates"

**Cause**: Network issue or GitHub unreachable  
**Fix**: Check internet connection; try again on next app restart  
**Impact**: Non-fatal; app continues working

### Installer doesn't launch

**Cause**: Download failed or temp file corrupted  
**Check logs**: See `logs/Saffron Automation*.log` for details  
**Fix**: Manually download and run latest installer from GitHub

### App didn't restart after installation

**Expected**: User must manually restart app after installer finishes  
**Why**: Inno Setup doesn't auto-restart the app (user chooses when)  
**Fix**: User clicks "Close and restart" after installer, or manually starts app

## Future Enhancements

Possible additions (not in current implementation):

- Automatic restart after installation (`/SILENT /NORESTART` flags)
- Silent updates in background (requires admin privilege)
- Update scheduling (check at specific times)
- Rollback to previous version if issues occur
- Analytics on update adoption rates

## Integration Checklist

- [x] `app/updater.py` — Core update logic
- [x] `ui/update_notification_dialog.py` — User notification UI
- [x] `ui/main_window.py` — Background check + dialog handling
- [x] Logging throughout updater for debugging
- [x] Non-blocking background thread
- [x] Graceful error handling
- [x] Data preservation (already safe)
- [x] Documentation (this file)

## File Locations (V2.0 Development)

```
Saffron Automation v2.0 - Development/
├── app/
│   ├── version.py              (bump version here for releases)
│   └── updater.py              (core update logic)
├── ui/
│   ├── main_window.py          (background check + dialog)
│   └── update_notification_dialog.py  (user-facing notification)
├── installer/
│   └── saffron_validator.iss   (bump MyAppVersion here too)
└── UPDATER_README.md           (this file)
```

## Related Files

- **app/config.py** — Explains data directory structure (why updates don't lose data)
- **app/logging_config.py** — Logging setup (update logs go to logs/ folder)
- **build_exe.ps1** — How to build the .exe
- **README.md** — General project documentation
