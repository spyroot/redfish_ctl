"""redfish_ctl package alias and endpoint configuration contracts.

The legacy package import resolves to the real redfish_ctl modules. Endpoint
credentials use canonical CLI namespace fields and canonical ``REDFISH_*``
environment variables.
"""
import sys

import pytest

from redfish_ctl.config import endpoint_defaults

_ENDPOINT_ENV = (
    "REDFISH_IP",
    "REDFISH_USERNAME",
    "REDFISH_PASSWORD",
    "REDFISH_PORT",
)


def _clear_endpoint_env(monkeypatch):
    """Remove endpoint env vars so tests never inherit shell state.

    :param monkeypatch: pytest monkeypatch fixture.
    :return: None.
    """
    for name in _ENDPOINT_ENV:
        monkeypatch.delenv(name, raising=False)


def _parse_root_cli(monkeypatch, *flags):
    """Parse root CLI flags without registering or executing commands."""
    from redfish_ctl import redfish_main as rm

    parsed = []
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setattr(rm, "create_cmd_tree", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(rm, "is_local_command", lambda _args: True)
    monkeypatch.setattr(
        rm,
        "main",
        lambda args, _commands, _manager_cls: parsed.append(args),
    )
    monkeypatch.setattr(sys, "argv", ["redfish_ctl", *flags])

    rm.redfish_main_ctl()

    assert len(parsed) == 1
    return parsed[0]


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


def test_endpoint_defaults_ignore_retired_vendor_endpoint_name(monkeypatch):
    """A retired vendor-specific endpoint variable is not configuration."""
    _clear_endpoint_env(monkeypatch)
    monkeypatch.setenv("IDRAC_" + "IP", "198.51.100.20")

    defaults = endpoint_defaults()

    assert defaults.host == ""


def test_env_default_when_none_set(monkeypatch):
    """Neither set -> the provided default."""
    _clear_endpoint_env(monkeypatch)
    assert endpoint_defaults().username == "root"


def test_root_endpoint_flags_set_only_canonical_attrs(monkeypatch):
    """Root endpoint flags populate the canonical parser destinations."""
    parsed = _parse_root_cli(
        monkeypatch,
        "--host", "203.0.113.10",
        "--username", "admin",
        "--password", "secret",
        "--port", "8443",
    )

    assert parsed.redfish_host == "203.0.113.10"
    assert parsed.redfish_username == "admin"
    assert parsed.redfish_password == "secret"
    assert parsed.redfish_port == 8443


def test_legacy_root_endpoint_flag_is_rejected(monkeypatch):
    """Dell-specific endpoint flags no longer leak into the shared root CLI."""
    retired_flag = "--" + "idrac" + "_ip"
    with pytest.raises(SystemExit):
        _parse_root_cli(monkeypatch, retired_flag, "203.0.113.10")


def test_root_connection_args_are_canonical():
    """Only canonical parser destinations are stripped before dispatch."""
    from redfish_ctl.redfish_main import _ROOT_CONNECTION_ARGS

    assert _ROOT_CONNECTION_ARGS == {
        "message_type",
        "redfish_host",
        "redfish_username",
        "redfish_password",
        "redfish_port",
    }


def test_use_http_canonical_flag_sets_use_http(monkeypatch):
    """The canonical --use-http flag enables HTTP transport."""
    args = _parse_root_cli(monkeypatch, "--use-http")

    assert args.use_http is True


def test_use_http_legacy_alias_sets_same_destination(monkeypatch):
    """The legacy --use_http alias retains its existing behavior."""
    args = _parse_root_cli(monkeypatch, "--use_http")

    assert args.use_http is True


def test_use_http_help_lists_canonical_flag_first(monkeypatch, capsys):
    """CLI help presents the hyphenated spelling as the canonical form."""
    from redfish_ctl import redfish_main as rm

    _clear_endpoint_env(monkeypatch)
    monkeypatch.setattr(rm, "create_cmd_tree", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(sys, "argv", ["redfish_ctl", "--help"])

    with pytest.raises(SystemExit) as exit_info:
        rm.redfish_main_ctl()

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--use-http" in help_text
    assert "--use_http" in help_text
    assert help_text.index("--use-http") < help_text.index("--use_http")
