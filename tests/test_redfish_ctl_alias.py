"""redfish_ctl compat alias + REDFISH_* env vars (rename phases 1 & 2).

Phase 1: `import redfish_ctl` and `from redfish_ctl.<sub> import ...` resolve to the real
idrac_ctl modules (same objects). Phase 2: endpoint/credentials read REDFISH_* first,
falling back to the legacy IDRAC_* names.
"""
import pytest

from redfish_ctl.config import ConfigurationConflict, endpoint_defaults

_ENDPOINT_ENV = (
    "REDFISH_IP",
    "REDFISH_USERNAME",
    "REDFISH_PASSWORD",
    "REDFISH_PORT",
    "IDRAC_IP",
    "IDRAC_USERNAME",
    "IDRAC_PASSWORD",
    "IDRAC_PORT",
)


def _clear_endpoint_env(monkeypatch):
    """Remove endpoint env vars so tests never inherit shell state.

    :param monkeypatch: pytest monkeypatch fixture.
    :return: None.
    """
    for name in _ENDPOINT_ENV:
        monkeypatch.delenv(name, raising=False)


def test_idrac_ctl_is_redfish_ctl_alias():
    """`import idrac_ctl` resolves to the same package object as redfish_ctl."""
    import idrac_ctl
    import redfish_ctl
    assert idrac_ctl is redfish_ctl


def test_idrac_ctl_submodule_is_same_object():
    """`from idrac_ctl.<sub> import X` returns the identical object as redfish_ctl's."""
    from idrac_ctl.redfish_manager import CommandResult as aliased

    from redfish_ctl.redfish_manager import CommandResult as real
    assert aliased is real


def test_endpoint_defaults_use_redfish_names(monkeypatch):
    """REDFISH_* values populate the canonical endpoint defaults."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("REDFISH_IP", "203.0.113.10")
    monkeypatch.setenv("REDFISH_USERNAME", "admin")
    monkeypatch.setenv("REDFISH_PASSWORD", "secret")
    monkeypatch.setenv("REDFISH_PORT", "8443")

    defaults = endpoint_defaults()

    assert defaults.host == "203.0.113.10"
    assert defaults.username == "admin"
    assert defaults.password == "secret"
    assert defaults.port == 8443


def test_endpoint_defaults_fall_back_to_idrac(monkeypatch):
    """With REDFISH_* unset, the legacy IDRAC_* value is used."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("IDRAC_IP", "198.51.100.20")
    monkeypatch.setenv("IDRAC_PORT", "8443")

    defaults = endpoint_defaults()

    assert defaults.host == "198.51.100.20"
    assert defaults.port == 8443


@pytest.mark.parametrize(
    ("redfish_name", "redfish_value", "idrac_name", "idrac_value"),
    [
        ("REDFISH_IP", "203.0.113.10", "IDRAC_IP", "198.51.100.20"),
        ("REDFISH_USERNAME", "admin", "IDRAC_USERNAME", "root"),
        ("REDFISH_PASSWORD", "secret-a", "IDRAC_PASSWORD", "secret-b"),
        ("REDFISH_PORT", "443", "IDRAC_PORT", "8443"),
    ],
)
def test_endpoint_defaults_reject_conflicting_aliases(
        monkeypatch, redfish_name, redfish_value, idrac_name, idrac_value):
    """Different canonical and legacy endpoint values fail closed."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv(redfish_name, redfish_value)
    monkeypatch.setenv(idrac_name, idrac_value)

    with pytest.raises(ConfigurationConflict):
        endpoint_defaults()


def test_env_default_when_none_set(monkeypatch):
    """Neither set -> the provided default."""
    _clear_endpoint_env(monkeypatch)
    assert endpoint_defaults().username == "root"


def test_legacy_cli_namespace_attrs_mirror_canonical_names():
    """Parsed args retain idrac_* attrs for subcommands that still read them."""
    from argparse import Namespace

    from redfish_ctl.redfish_main import _sync_legacy_endpoint_attrs

    args = Namespace(
        redfish_host="203.0.113.10",
        redfish_username="admin",
        redfish_password="secret",
        redfish_port=8443,
    )

    _sync_legacy_endpoint_attrs(args)

    assert args.idrac_ip == "203.0.113.10"
    assert args.idrac_username == "admin"
    assert args.idrac_password == "secret"
    assert args.idrac_port == 8443


def test_endpoint_alias_action_sets_legacy_attrs_without_sync():
    """Root endpoint flags populate canonical and legacy attrs during parsing."""
    import argparse

    from redfish_ctl.redfish_main import _EndpointAliasAction

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host", dest="redfish_host", default="",
        action=_EndpointAliasAction, legacy_dest="idrac_ip")
    parser.add_argument(
        "--port", dest="redfish_port", default=443, type=int,
        action=_EndpointAliasAction, legacy_dest="idrac_port")
    parser.set_defaults(idrac_ip="", idrac_port=443)

    parsed = parser.parse_args(["--host", "203.0.113.10", "--port", "8443"])
    defaults = parser.parse_args([])

    assert parsed.redfish_host == "203.0.113.10"
    assert parsed.idrac_ip == "203.0.113.10"
    assert parsed.redfish_port == 8443
    assert parsed.idrac_port == 8443
    assert defaults.redfish_host == ""
    assert defaults.idrac_ip == ""
