"""Draft a categorized changelog entry from commits since the last tag.

Groups commit subjects by keyword into rough buckets (sync systems, bug
fixes, CI/build, docs, other) so a release doesn't start from a raw `git
log`. This is a DRAFT for a human to review and edit before pasting into
CHANGELOG.md -- it does not commit, tag, or touch any file itself.

Usage:  python draft_changelog.py [<since-tag>]   (default: latest tag)
"""

import subprocess
import sys

CATEGORIES = [
    ("Sync systems", ("sync", "manifest", "recipient", "parameter")),
    ("Bug fixes", ("fix", "revert", "re-notification", "bug")),
    ("CI / build", ("ci", "workflow", "macos", "pytest", "build", "timeout", "pyinstaller")),
    ("Docs", ("docs", "handoff", "readme", "changelog")),
]


def commits_since(since_tag: str) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--format=%s", f"{since_tag}..HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return [line for line in out.splitlines() if line]


def latest_tag() -> str:
    return subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"], capture_output=True, text=True, check=True,
    ).stdout.strip()


def categorize(subjects: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {name: [] for name, _ in CATEGORIES}
    buckets["Other"] = []
    for subject in subjects:
        lower = subject.lower()
        for name, keywords in CATEGORIES:
            if any(kw in lower for kw in keywords):
                buckets[name].append(subject)
                break
        else:
            buckets["Other"].append(subject)
    return buckets


def render(since_tag: str, buckets: dict[str, list[str]]) -> str:
    lines = [f"## Unreleased (since {since_tag})", "", "> DRAFT -- review and edit before publishing.", ""]
    for name, subjects in buckets.items():
        if not subjects:
            continue
        lines.append(f"### {name}")
        lines.extend(f"- {s}" for s in subjects)
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else latest_tag()
    subjects = commits_since(tag)
    if not subjects:
        print(f"No commits since {tag}.")
        sys.exit(0)
    print(render(tag, categorize(subjects)))
