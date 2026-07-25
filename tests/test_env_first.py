"""env_first: canonical REDFISH_* first, IDRAC_* a deprecated alias, conflict-aware.

A setting is resolved once: the canonical name wins, the legacy name still works
(with a deprecation warning), both set to the same value is fine, and both set to
*different* values is a hard configuration conflict — no silent override. See the
registry specs/config/environment.yaml.
"""
import warnings

import pytest

from redfish_ctl.config import ConfigurationConflict, required_dmtf_sim_endpoint
from redfish_ctl.redfish_shared import env_first


def test_conflict_when_both_set_differently(monkeypatch):
    """Canonical and legacy set to different values -> ConfigurationConflict.

    This is the behavior that replaces silent 'REDFISH_* wins': a mismatched pair
    is a misconfiguration the operator must resolve, not something to paper over.
    """
    monkeypatch.setenv("REDFISH_HTTP_TIMEOUT", "5")
    monkeypatch.setenv("IDRAC_HTTP_TIMEOUT", "30")
    with pytest.raises(ConfigurationConflict) as exc:
        env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT", default="30")
    msg = str(exc.value)
    assert "Configuration conflict" in msg
    assert "Use only REDFISH_HTTP_TIMEOUT" in msg


def test_both_set_same_value_ok(monkeypatch):
    """Both set to the same value -> the canonical value, no conflict."""
    monkeypatch.setenv("REDFISH_HTTP_TIMEOUT", "20")
    monkeypatch.setenv("IDRAC_HTTP_TIMEOUT", "20")
    assert env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT", default="30") == "20"


def test_falls_back_to_legacy_with_warning(monkeypatch):
    """With REDFISH_* unset, the legacy IDRAC_* value is used and a warning fires."""
    monkeypatch.delenv("REDFISH_HTTP_TIMEOUT", raising=False)
    monkeypatch.setenv("IDRAC_HTTP_TIMEOUT", "45")
    with pytest.warns(DeprecationWarning, match="IDRAC_HTTP_TIMEOUT is a deprecated alias"):
        assert env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT", default="30") == "45"


def test_canonical_only_no_warning(monkeypatch):
    """The canonical name alone -> its value, no deprecation warning."""
    monkeypatch.setenv("REDFISH_HTTP_TIMEOUT", "7")
    monkeypatch.delenv("IDRAC_HTTP_TIMEOUT", raising=False)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning would fail here
        assert env_first("REDFISH_HTTP_TIMEOUT", "IDRAC_HTTP_TIMEOUT", default="30") == "7"


def test_default_when_none_set(monkeypatch):
    """Neither set -> the provided default (even for empty-string names)."""
    monkeypatch.delenv("REDFISH_HTTP_POOL", raising=False)
    monkeypatch.delenv("IDRAC_HTTP_POOL", raising=False)
    assert env_first("REDFISH_HTTP_POOL", "IDRAC_HTTP_POOL", default="4") == "4"


def test_empty_string_value_is_honored(monkeypatch):
    """An explicitly empty value is a real value, not 'unset' -> not the default."""
    monkeypatch.setenv("REDFISH_HTTP_POOL", "")
    monkeypatch.delenv("IDRAC_HTTP_POOL", raising=False)
    assert env_first("REDFISH_HTTP_POOL", "IDRAC_HTTP_POOL", default="4") == ""


def test_default_is_none_without_arg(monkeypatch):
    """No default given -> None when nothing is set."""
    monkeypatch.delenv("REDFISH_NOPE", raising=False)
    assert env_first("REDFISH_NOPE") is None


def _clear_endpoint_env(monkeypatch):
    """Remove every endpoint env name so a test starts from a clean slate.

    :param monkeypatch: the pytest monkeypatch fixture.
    :return: None.
    """
    for name in ("REDFISH_IP", "IDRAC_IP", "REDFISH_PORT", "IDRAC_PORT"):
        monkeypatch.delenv(name, raising=False)


def test_dmtf_sim_endpoint_ok(monkeypatch):
    """Canonical REDFISH_IP/REDFISH_PORT present -> a validated http endpoint."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("REDFISH_IP", "dmtf-sim.dmtf-bmc.svc.cluster.local")
    monkeypatch.setenv("REDFISH_PORT", "80")
    ep = required_dmtf_sim_endpoint()
    assert ep.host == "dmtf-sim.dmtf-bmc.svc.cluster.local"
    assert ep.port == 80
    assert ep.is_http is True


def test_dmtf_sim_endpoint_missing_ip_fails_closed(monkeypatch):
    """Missing REDFISH_IP raises (fail-closed) — it never returns a partial value."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("REDFISH_PORT", "80")
    with pytest.raises(RuntimeError, match="REDFISH_IP is missing"):
        required_dmtf_sim_endpoint()


def test_dmtf_sim_endpoint_missing_port_fails_closed(monkeypatch):
    """Missing REDFISH_PORT raises (fail-closed)."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("REDFISH_IP", "sim")
    with pytest.raises(RuntimeError, match="REDFISH_PORT is missing"):
        required_dmtf_sim_endpoint()


def test_dmtf_sim_endpoint_rejects_url_scheme(monkeypatch):
    """A URL in REDFISH_IP is rejected — the endpoint is host+port, never a URL."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("REDFISH_IP", "http://sim")
    monkeypatch.setenv("REDFISH_PORT", "80")
    with pytest.raises(RuntimeError, match="not a URL scheme"):
        required_dmtf_sim_endpoint()


def test_dmtf_sim_endpoint_rejects_uri_path(monkeypatch):
    """A URI path in REDFISH_IP is rejected."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("REDFISH_IP", "sim/redfish")
    monkeypatch.setenv("REDFISH_PORT", "80")
    with pytest.raises(RuntimeError, match="must not contain a URI path"):
        required_dmtf_sim_endpoint()


def test_dmtf_sim_endpoint_rejects_non_integer_port(monkeypatch):
    """A non-integer port is rejected."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("REDFISH_IP", "sim")
    monkeypatch.setenv("REDFISH_PORT", "notaport")
    with pytest.raises(RuntimeError, match="must be an integer"):
        required_dmtf_sim_endpoint()


def test_dmtf_sim_endpoint_rejects_out_of_range_port(monkeypatch):
    """A port outside 1..65535 is rejected."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("REDFISH_IP", "sim")
    monkeypatch.setenv("REDFISH_PORT", "70000")
    with pytest.raises(RuntimeError, match="between 1 and 65535"):
        required_dmtf_sim_endpoint()


def test_dmtf_sim_endpoint_conflict_on_alias_mismatch(monkeypatch):
    """REDFISH_IP and IDRAC_IP set to different values -> ConfigurationConflict."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("REDFISH_IP", "sim-a")
    monkeypatch.setenv("IDRAC_IP", "sim-b")
    monkeypatch.setenv("REDFISH_PORT", "80")
    with pytest.raises(ConfigurationConflict):
        required_dmtf_sim_endpoint()
