"""Automatic update system for Saffron Automation.

Checks GitHub releases for newer versions, downloads updates, and launches the installer
with minimal user interaction. User data (database, settings, logs) is preserved across updates
since it's stored in %LOCALAPPDATA%, separate from the installed application files.

Version 2.0: channel-aware. app.version.CHANNEL selects which GitHub feed
gets checked, and the two channels are structurally incapable of seeing
each other's builds -- not just "shouldn't," but "can't":
  - "production" calls GET /releases/latest, which is GitHub's own
    latest-non-prerelease-non-draft endpoint -- a pre-release can never be
    returned from it, so a Production build can never be offered a
    Development build even if one exists.
  - "development" calls GET /releases (the full list) and keeps only
    entries GitHub itself marked as a pre-release -- a stable release can
    only reach a Development build if someone manually re-flags it as a
    pre-release on GitHub, which is not something this app does.
Each channel's fetch function is separate (_get_latest_stable_release /
_get_latest_prerelease) rather than one function with a runtime filter, so
there's no shared code path where a filtering bug could leak one channel's
release into the other.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple
from urllib.error import URLError
from urllib.request import urlopen

from loguru import logger

from app.version import APP_VERSION, CHANNEL


class ReleaseInfo(NamedTuple):
    """Information about a GitHub release."""

    version: str
    download_url: str
    release_url: str
    prerelease: bool


class UpdateStatus(NamedTuple):
    """Result of checking for updates."""

    available: bool
    current_version: str
    latest_version: str | None = None
    release_info: ReleaseInfo | None = None
    error: str | None = None


GITHUB_REPO = "dhairyagautam-creator/Saffron-Automation"
GITHUB_LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_LIST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=30"
INSTALLER_FILENAME = "Saffron Automation Setup.exe"
# What the installer asset's name must contain, ignoring spaces/dots/dashes/
# underscores and case -- GitHub silently rewrites spaces to dots on upload
# (e.g. "Saffron Automation Setup v1.1.0.exe" becomes
# "Saffron.Automation.Setup.v1.1.0.exe"), so matching on the literal
# space-separated filename missed every real release. Comparing normalized
# forms survives that rewrite, and any other separator variation a future
# manual upload might introduce.
_INSTALLER_NAME_TOKEN = "saffronautomationsetup"


def _normalize_asset_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version like '1.2.3' or '1.3.1-dev.0.2' into a tuple for
    comparison. The core X.Y.Z always occupies the first 3 slots; a 4th
    slot is 1 for a final release and 0 for any pre-release, so a final
    release always sorts above a pre-release of the same core version
    (standard semver pre-release ordering, e.g. "1.3.1" > "1.3.1-dev.0.9");
    any further digits found in a "-dev.X.Y"-style suffix are appended
    after that, so later dev builds of the same core version sort higher
    (e.g. "1.3.1-dev.0.2" > "1.3.1-dev.0.1"). Unparseable input falls back
    to the lowest possible value rather than raising, matching the
    previous function's fail-safe behavior."""
    text = (version_str or "").strip().lstrip("vV")
    core_part, _, suffix_part = text.partition("-")
    try:
        core = tuple(int(x) for x in core_part.split(".")[:3])
    except (ValueError, AttributeError):
        return (0, 0, 0, 0)
    core = core + (0,) * (3 - len(core))

    if not suffix_part:
        return core + (1,)

    suffix_numbers = tuple(int(n) for n in re.findall(r"\d+", suffix_part))
    return core + (0,) + suffix_numbers


def _release_info_from_payload(data: dict) -> ReleaseInfo | None:
    """Builds a ReleaseInfo from one GitHub API release object, or None if
    it has no matching installer asset attached."""
    version = data.get("tag_name", "").lstrip("vV")
    download_url = None
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.lower().endswith(".exe") and _INSTALLER_NAME_TOKEN in _normalize_asset_name(name):
            download_url = asset.get("browser_download_url")
            break

    if not download_url:
        logger.warning(f"Release {version} has no installer attachment")
        return None

    return ReleaseInfo(
        version=version,
        download_url=download_url,
        release_url=data.get("html_url", ""),
        prerelease=data.get("prerelease", False),
    )


def _get_latest_stable_release() -> ReleaseInfo | None:
    """Production channel: GitHub's own latest-non-prerelease-non-draft
    endpoint. The explicit prerelease check below is a defensive
    belt-and-suspenders on top of that endpoint's documented guarantee,
    not a substitute for it -- if GitHub's contract ever changed, this
    would still refuse to treat a pre-release as installable here."""
    try:
        with urlopen(GITHUB_LATEST_RELEASE_URL, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except URLError as e:
        logger.warning(f"Failed to fetch latest release from GitHub: {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error checking for updates: {e}")
        return None

    if data.get("prerelease"):
        logger.warning("GET /releases/latest unexpectedly returned a pre-release -- ignoring it.")
        return None
    return _release_info_from_payload(data)


def _get_latest_prerelease() -> ReleaseInfo | None:
    """Development channel: the full releases list, filtered to entries
    GitHub itself marked as a pre-release (and not a draft), highest
    version wins. Never falls back to a stable release if no pre-release
    exists -- returning None (no update found) is correct in that case,
    not silently offering a Production build instead."""
    try:
        with urlopen(GITHUB_RELEASES_LIST_URL, timeout=5) as response:
            releases = json.loads(response.read().decode("utf-8"))
    except URLError as e:
        logger.warning(f"Failed to fetch release list from GitHub: {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error checking for updates: {e}")
        return None

    candidates = [r for r in releases if r.get("prerelease") and not r.get("draft")]
    if not candidates:
        logger.info("No Development pre-release found on GitHub.")
        return None

    latest_payload = max(candidates, key=lambda r: _parse_version(r.get("tag_name", "")))
    return _release_info_from_payload(latest_payload)


def _get_latest_release() -> ReleaseInfo | None:
    """Dispatches to the current build's channel -- see module docstring
    for why these two paths never see each other's releases."""
    if CHANNEL == "development":
        return _get_latest_prerelease()
    return _get_latest_stable_release()


def check_for_updates() -> UpdateStatus:
    """Check if a newer version is available on GitHub.

    Returns UpdateStatus with available=True and release_info if an update exists.
    """
    logger.info(f"Checking for updates (current version: {APP_VERSION})...")

    release = _get_latest_release()
    if not release:
        # No release found is a normal, expected state on the Development
        # channel (nothing has been published as a pre-release yet) -- not
        # necessarily a network failure, so the message shouldn't claim it
        # is one. The underlying reason (network error vs. genuinely none
        # found) is already logged by whichever _get_latest_* function ran.
        no_release_message = (
            "No Development pre-release is available on GitHub yet."
            if CHANNEL == "development"
            else "Could not reach GitHub to check for updates."
        )
        return UpdateStatus(
            available=False,
            current_version=APP_VERSION,
            error=no_release_message,
        )

    current = _parse_version(APP_VERSION)
    latest = _parse_version(release.version)

    if latest > current:
        logger.info(f"Update available: {APP_VERSION} → {release.version}")
        return UpdateStatus(
            available=True,
            current_version=APP_VERSION,
            latest_version=release.version,
            release_info=release,
        )
    else:
        logger.info(f"Already on latest version: {APP_VERSION}")
        return UpdateStatus(
            available=False,
            current_version=APP_VERSION,
            latest_version=release.version,
        )


def download_installer(download_url: str) -> Path | None:
    """Download the installer to a temporary location.

    Returns the path to the downloaded file, or None if download fails.
    """
    try:
        logger.info(f"Downloading installer from {download_url}...")
        temp_dir = Path(tempfile.gettempdir()) / "saffron_updates"
        temp_dir.mkdir(exist_ok=True, parents=True)
        temp_file = temp_dir / INSTALLER_FILENAME

        with urlopen(download_url, timeout=30) as response:
            with open(temp_file, "wb") as f:
                f.write(response.read())

        logger.info(f"Installer downloaded to {temp_file}")
        return temp_file
    except Exception as e:
        logger.error(f"Failed to download installer: {e}")
        return None


def launch_installer(installer_path: Path) -> bool:
    """Launch the installer executable with minimal user interaction.

    The installer itself was built with Inno Setup and handles the update gracefully:
    - It detects the app is already installed
    - Offers an upgrade option
    - Preserves all user data (database, settings, logs in %LOCALAPPDATA%)
    - Cleans up old files
    - Can run with /SILENT for completely unattended installs (optional future enhancement)

    Returns True if the installer was launched successfully.
    """
    # The installer asset is a Windows Inno Setup .exe -- it cannot run on
    # macOS/Linux, and `shell=True` here would just hand a Windows path to a
    # POSIX shell. Refuse rather than launch something broken; the UI directs
    # macOS users to the .dmg from the release page instead (see
    # ui/main_window.py's install branch). A safe no-op beats a broken update.
    if sys.platform != "win32":
        logger.warning(
            "launch_installer skipped: the Windows .exe installer cannot run on "
            f"{sys.platform}. Install the macOS .dmg from the release page instead."
        )
        return False

    try:
        if not installer_path.exists():
            logger.error(f"Installer file not found: {installer_path}")
            return False

        logger.info(f"Launching installer: {installer_path}")

        # Start the installer as a separate process.
        # Don't wait for it to complete -- the user can continue working until it's ready,
        # and the app will restart automatically after installation.
        subprocess.Popen([str(installer_path)], shell=True)
        return True
    except Exception as e:
        logger.error(f"Failed to launch installer: {e}")
        return False


def perform_update(release_info: ReleaseInfo) -> bool:
    """Download and launch the installer for the given release.

    Returns True if the installer was successfully launched.
    """
    installer_path = download_installer(release_info.download_url)
    if not installer_path:
        return False

    return launch_installer(installer_path)
