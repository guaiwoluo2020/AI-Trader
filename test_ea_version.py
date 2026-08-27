from fastapi import HTTPException

from ea_auth import ensure_supported_ea_version


def test_minimum_ea_version_is_accepted():
    assert ensure_supported_ea_version("2.0.7") == "2.0.7"
    assert ensure_supported_ea_version("2.07") == "2.07"
    assert ensure_supported_ea_version("2.0.8") == "2.0.8"


def test_old_or_missing_ea_version_is_rejected():
    for version in ("", "2.0.6", "2.06", "1.99", "invalid"):
        try:
            ensure_supported_ea_version(version)
        except HTTPException as exc:
            assert exc.status_code == 426
            assert exc.detail["required_version"] == "2.0.7"
        else:
            raise AssertionError(f"version {version!r} should be rejected")
