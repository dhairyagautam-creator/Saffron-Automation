# Saffron Automation

An enterprise desktop application for Saffron Formulations, organized as a single app with
multiple automation modules launched from a Home screen. It currently includes three modules:

- **Path Validator** — the field-force GPS call-report fraud detector (import, rule engine,
  findings, manager email notifications, analytics dashboard with heat map).
- **Inventory Monitoring** — replenishment thresholds and stock monitoring across branches
  (inventory + previous-month-sales upload, thresholds, replenishment).
- **Payment Analytics** — historical customer payment-behavior analytics with a rolling
  six-month window, and the Collections Action Center for currently outstanding invoices.

Future automation tools are added as additional modules under this same app rather than as new
standalone apps.

## Project Structure

```
Saffron Employee Detector/
├── app/              # App-wide configuration, logging setup, and business logic/services
├── database/         # SQLAlchemy models, connection setup, and startup migrations
├── rules/            # Path Validator's fraud detection rules
├── reports/          # Report-related assets
├── ui/               # CustomTkinter desktop UI (one module per *_module.py + its pages)
├── installer/        # Inno Setup script for the distributable Windows installer
├── tests/            # Test suite
├── main.py           # Application entry point
├── build_exe.ps1     # Builds the PyInstaller executable (see "Releasing a new version")
├── requirements.txt
└── README.md
```

## Requirements

- Python 3.12

## Setup

### 1. Create a virtual environment

```powershell
py -3.12 -m venv .venv
```

### 2. Activate the virtual environment

On Windows (PowerShell):

```powershell
.venv\Scripts\Activate.ps1
```

On Windows (cmd.exe):

```cmd
.venv\Scripts\activate.bat
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

```powershell
pip install -r requirements.txt
```

## Running the Application

```powershell
python main.py
```

This launches Saffron Automation to a Home screen with a card for each module (Path Validator,
Inventory Monitoring, Payment Analytics). Every module has a "← Back to Home" control to return
to the Home screen without restarting the app.

## Logs

Application logs are written to the `logs/` directory (created automatically on first run) and
also printed to the console.

## Releasing a new version

The application ships to end users as a Windows installer (built with
[Inno Setup](https://jrsoftware.org/isinfo.php)), not as source or a raw `.exe` — the installer
bundles everything needed to run, including the Python interpreter, so recipients never install
anything else first.

1. **Bump the version.** Edit `app/version.py` (`APP_VERSION`, `BUILD_DATE`) and
   `installer/saffron_validator.iss` (`MyAppVersion`, and the `Output:` comment above it) to
   match — both need to agree. Also check `CHANNEL` (`app/version.py`) and `MyChannel` (the
   `.iss` file) are set to the channel you actually intend (`"production"` or `"development"`)
   — see [UPDATER_README.md](UPDATER_README.md#release-channels-version-20) for the full
   channel system, including the GitHub pre-release step Development builds require.
2. **Rebuild the executable:**
   ```powershell
   .\build_exe.ps1
   ```
   (equivalent to `python -m PyInstaller --noconfirm --clean "Saffron Automation.spec"` — deliberately
   *not* the `pyinstaller.exe` launcher script; see the comments at the top of `build_exe.ps1` for why).
   This produces
   `dist\Saffron Automation\Saffron Automation.exe` plus its `_internal\` support folder (the two
   must always travel together). Close any running instance of the app first — Windows locks the
   .exe and its DLLs while it's open, which makes the build fail partway through.
3. **Compile the installer:**
   ```powershell
   & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\saffron_validator.iss
   ```
   (If Inno Setup isn't installed yet: `winget install JRSoftware.InnoSetup`.)
4. **Find the output** in `installer_output\Saffron Automation Setup v<version>.exe` — this is
   the single file to distribute. Running it installs the app, creates Desktop + Start Menu
   shortcuts named "Saffron Automation", registers it in Add/Remove Programs, and optionally
   launches it on finish. No admin rights are required — it installs per-user under
   `%LocalAppData%\Programs\Saffron Automation`.

Do not change `installer/saffron_validator.iss`'s `MyAppId` between releases — Windows uses it to
recognize an install as an upgrade of the same application rather than a separate one.

## Auto-Update System (Version 2.0+)

The application includes an automatic update system that:
- Checks for newer versions on startup (non-blocking, background thread)
- Notifies the user if an update is available
- Downloads the installer from GitHub releases
- Launches the installer with one click
- Preserves all user data (database, settings, logs) during updates

### How It Works

1. On startup, the app queries GitHub for the latest release
2. If a newer version is available, a notification dialog appears
3. User can choose to "Install Now", "Later", or "View Release Notes"
4. The installer is downloaded to a temp location and launched
5. Inno Setup recognizes it as an upgrade and preserves user data
6. User restarts the app when ready (can continue working while installer prepares)

### Releasing Updates

See [UPDATER_README.md](UPDATER_README.md) for the complete guide to:
- Bumping the version
- Building release artifacts
- Creating a GitHub release
- Verifying the auto-update system works

**Key points:**
- Version is in `app/version.py` (single source of truth)
- Built installer goes in `installer_output/`
- Upload to GitHub release as attachment
- Next app start detects the update automatically
