"""env_first: REDFISH_* names win, IDRAC_* is the fallback, else the default.

Backs the env-var consistency pass — every tuning knob (HTTP timeout/retries/pool/
backoff, discovery retries/backoff/pace, exporter credential file) reads the
going-forward REDFISH_* name first while the legacy IDRAC_* name still works.
"""
from redfish_ctl.redfish_shared import env_first


def test_prefers_first_name(monkeypatch):
    """The REDFISH_* name (passed first) wins when both are set."""
    monkeypatch.setenv("REDFISH_HTTP_TIMEOUT", "5")
    monkeypatch.setenv("IDRAC_HTTP_TIMEOUT", "30")
    assert env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT", default="30") == "5"


def test_falls_back_to_legacy(monkeypatch):
    """With REDFISH_* unset, the legacy IDRAC_* value is used."""
    monkeypatch.delenv("REDFISH_HTTP_TIMEOUT", raising=False)
    monkeypatch.setenv("IDRAC_HTTP_TIMEOUT", "45")
    assert env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT", default="30") == "45"


def test_default_when_none_set(monkeypatch):
    """Neither set -> the provided default (even for empty-string names)."""
    monkeypatch.delenv("REDFISH_HTTP_POOL", raising=False)
    monkeypatch.delenv("IDRAC_HTTP_POOL", raising=False)
    assert env_first("REDFISH_HTTP_POOL", "IDRAC_HTTP_POOL", default="4") == "4"


def test_empty_string_value_is_honored(monkeypatch):
    """An explicitly empty value is a real value, not 'unset' -> not the default."""
    monkeypatch.setenv("REDFISH_HTTP_POOL", "")
    assert env_first("REDFISH_HTTP_POOL", "IDRAC_HTTP_POOL", default="4") == ""


def test_default_is_none_without_arg(monkeypatch):
    """No default given -> None when nothing is set."""
    monkeypatch.delenv("REDFISH_NOPE", raising=False)
    assert env_first("REDFISH_NOPE") is None
