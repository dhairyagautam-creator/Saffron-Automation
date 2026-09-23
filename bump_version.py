"""Bump the app version in the two places it must agree, in one shot.

`app/version.py`'s APP_VERSION and `installer/saffron_validator.iss`'s
MyAppVersion have to match for every release; being hand-edited in two
files is exactly how they go out of sync. This edits both (plus
version.py's BUILD_DATE and the .iss file's `Output:` comment) and does
nothing else -- no git tag, no commit, no changelog (see RELEASING.md for
the rest of the release checklist).

Usage:  python bump_version.py 3.1.0
"""

import re
import sys
from datetime import date
from pathlib import Path

VERSION_PY = Path("app/version.py")
ISS_FILE = Path("installer/saffron_validator.iss")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-dev\.\d+\.\d+)?$")


def bump(new_version: str, today: str) -> None:
    if not VERSION_RE.match(new_version):
        raise SystemExit(f"'{new_version}' doesn't look like a version (expected e.g. 3.1.0 or 3.1.0-dev.0.1)")

    py_text = VERSION_PY.read_text(encoding="utf-8")
    py_text, n1 = re.subn(r'APP_VERSION = ".*"', f'APP_VERSION = "{new_version}"', py_text)
    py_text, n2 = re.subn(r'BUILD_DATE = ".*"', f'BUILD_DATE = "{today}"', py_text)
    if n1 != 1 or n2 != 1:
        raise SystemExit(f"Expected exactly one APP_VERSION and one BUILD_DATE line in {VERSION_PY}")
    VERSION_PY.write_text(py_text, encoding="utf-8")

    # The "Output:" comment above MyAppVersion already references the
    # {MyAppVersion} macro, not a literal version, so it never goes stale.
    iss_text = ISS_FILE.read_text(encoding="utf-8")
    iss_text, n3 = re.subn(r'#define MyAppVersion ".*"', f'#define MyAppVersion "{new_version}"', iss_text)
    if n3 != 1:
        raise SystemExit(f"Expected exactly one MyAppVersion define in {ISS_FILE}")
    ISS_FILE.write_text(iss_text, encoding="utf-8")

    print(f"app/version.py: APP_VERSION={new_version!r}, BUILD_DATE={today!r}")
    print(f"installer/saffron_validator.iss: MyAppVersion={new_version!r}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python bump_version.py <version>  (e.g. 3.1.0)")
    bump(sys.argv[1], date.today().isoformat())
