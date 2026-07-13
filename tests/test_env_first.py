"""env_first helper behavior for REDFISH_* runtime settings."""

from redfish_ctl.redfish_shared import env_first


def test_prefers_first_name(monkeypatch):
    """The first configured name wins when several candidates are provided."""
    monkeypatch.setenv("REDFISH_HTTP_TIMEOUT", "5")
    monkeypatch.setenv("REDFISH_HTTP_TIMEOUT_FALLBACK", "30")
    assert env_first(
        "REDFISH_HTTP_TIMEOUT",
        "REDFISH_HTTP_TIMEOUT_FALLBACK",
        default="30",
    ) == "5"


def test_default_when_none_set(monkeypatch):
    """Neither set -> the provided default."""
    monkeypatch.delenv("REDFISH_HTTP_POOL", raising=False)
    assert env_first("REDFISH_HTTP_POOL", default="4") == "4"


def test_empty_string_value_is_honored(monkeypatch):
    """An explicitly empty value is a real value, not unset."""
    monkeypatch.setenv("REDFISH_HTTP_POOL", "")
    assert env_first("REDFISH_HTTP_POOL", default="4") == ""


def test_default_is_none_without_arg(monkeypatch):
    """No default given -> None when nothing is set."""
    monkeypatch.delenv("REDFISH_NOPE", raising=False)
    assert env_first("REDFISH_NOPE") is None
