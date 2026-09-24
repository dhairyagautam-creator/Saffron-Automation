"""Regression test for a real production bug: v3.0.0's GitHub release
asset was uploaded as "Saffron-Automation-Windows-3.0.0.exe" (the CI
workflow's own artifact-staging name -- see .github/workflows/build.yml's
"Stage artifact with platform name" step), which contains no "setup" at
all. The updater's installer-asset matching required "setup" in the
normalized name, so it silently matched zero releases from v3.0.0 onward
-- every installed build older than that could no longer find ANY update,
not just that one. Confirmed live against the real GitHub API before this
fix (app.updater._get_latest_stable_release() returned None for the real,
published v3.0.0 release with an installer already attached).
"""

from app.updater import _normalize_asset_name, _release_info_from_payload, _INSTALLER_NAME_TOKEN


def _payload(asset_name: str, prerelease: bool = False) -> dict:
    return {
        "tag_name": "v3.0.1",
        "html_url": "https://github.com/example/repo/releases/tag/v3.0.1",
        "prerelease": prerelease,
        "assets": [{"name": asset_name, "browser_download_url": f"https://example.com/{asset_name}"}],
    }


def test_current_ci_asset_naming_is_matched():
    """The actual bug: CI's real staged filename must be found."""
    info = _release_info_from_payload(_payload("Saffron-Automation-Windows-3.0.1.exe"))
    assert info is not None
    assert info.download_url.endswith("Saffron-Automation-Windows-3.0.1.exe")


def test_legacy_setup_naming_is_still_matched():
    """Older releases (v2.4.0 and earlier) used Inno Setup's own output
    name, rewritten by GitHub's dot-substitution on upload -- must keep
    working, not just the new CI naming."""
    info = _release_info_from_payload(_payload("Saffron.Automation.Setup.v2.4.0.exe"))
    assert info is not None


def test_unrelated_asset_is_not_matched():
    info = _release_info_from_payload(_payload("some-other-tool.exe"))
    assert info is None


def test_non_exe_asset_is_not_matched_even_if_name_matches():
    """A macOS .dmg release asset shares the app's name but must never be
    offered as a Windows installer download."""
    info = _release_info_from_payload(_payload("Saffron-Automation-macOS-3.0.1.dmg"))
    assert info is None


def test_installer_name_token_is_the_looser_form():
    """Pins the actual fix: the token must not require "setup" anymore."""
    assert _INSTALLER_NAME_TOKEN == "saffronautomation"
    assert "setup" not in _INSTALLER_NAME_TOKEN
    assert _INSTALLER_NAME_TOKEN in _normalize_asset_name("Saffron-Automation-Windows-3.0.1.exe")
