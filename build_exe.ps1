# Builds Saffron Automation into a standalone Windows executable, from
# "Saffron Automation.spec" (the single source of truth for the build
# config -- assets bundling, icon, windowed mode; see that file) rather
# than reconstructing the same flags here, so the two can never drift
# apart.
#
# Usage:  .\build_exe.ps1
#
# Output: dist\Saffron Automation\Saffron Automation.exe (plus its
# _internal support folder, which must stay alongside the .exe — don't
# move one without the other). The database and logs are NOT bundled
# with the build -- see app/config.py for where they're created instead
# (a per-user, always-writable location, not next to the .exe).
#
# Invoked as `python -m PyInstaller`, NOT the `.venv\Scripts\pyinstaller.exe`
# console-script launcher -- that launcher is a pip-generated stub that
# embeds an absolute path to whichever python.exe existed at the moment
# `pip install pyinstaller` ran, baked into the executable itself. If this
# venv folder is ever copied to a new location (as this project's own
# backup/dev-copy workflow does), that embedded path still points at the
# OLD location and silently keeps using ITS site-packages -- so newly
# installed packages (e.g. this project's Supabase dependencies) appear
# completely invisible to PyInstaller's analysis even though `pip install`
# and plain `python.exe` both correctly see them. `python.exe`/`pythonw.exe`
# themselves don't have this problem (they resolve their environment via
# `pyvenv.cfg`, found relative to their own real location), so invoking
# PyInstaller as a module through them sidesteps it entirely. `--clean`
# additionally clears PyInstaller's own build cache, which can otherwise
# mask a real dependency change (like this file's own hiddenimports list)
# behind a stale cached analysis from a previous build.

& ".\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean "Saffron Automation.spec"
