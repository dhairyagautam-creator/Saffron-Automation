"""Fail loudly if .env is missing SUPABASE_URL or SUPABASE_ANON_KEY.

The v3.0.0 Windows installer shipped with an empty .env: CI's "Write .env"
step wrote blank values from blank repo secrets and still reported green,
and nobody noticed until a fresh install failed at the login screen. Both
CI jobs run this right after writing .env, and build_exe.ps1 runs it before
a local build, so an empty value is a red build instead of a broken app.

Usage:  python check_env.py [path-to-.env]      (default: .env)
Exit code 0 = OK, 1 = problem (message says which key). Never prints values.
Stdlib only on purpose -- it runs before `pip install` in CI.
"""

import sys
from pathlib import Path

REQUIRED = ("SUPABASE_URL", "SUPABASE_ANON_KEY")


def check(text: str) -> list[str]:
    """Return a list of problems with the given .env contents (empty = OK)."""
    values = {}
    for line in text.lstrip("﻿").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    problems = [f"{key} is missing or empty" for key in REQUIRED if not values.get(key)]
    url = values.get("SUPABASE_URL", "")
    if url and not url.startswith("https://"):
        problems.append("SUPABASE_URL doesn't start with https://")
    return problems


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    problems = check(path.read_text(encoding="utf-8")) if path.exists() else [f"{path} not found"]
    for p in problems:
        # ::error:: renders as a red annotation on the GitHub Actions run page.
        print(f"::error::{path}: {p} -- check the repo secrets (see RELEASING.md)")
    if not problems:
        print(f"{path}: {', '.join(REQUIRED)} present")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
