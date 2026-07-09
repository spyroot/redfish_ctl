"""redfish_ctl compat alias + REDFISH_* env vars (rename phases 1 & 2).

Phase 1: `import redfish_ctl` and `from redfish_ctl.<sub> import ...` resolve to the real
idrac_ctl modules (same objects). Phase 2: endpoint/credentials read REDFISH_* first,
falling back to the legacy IDRAC_* names.
"""
from idrac_ctl.idrac_main import _env


def test_redfish_ctl_is_idrac_ctl_alias():
    """`import redfish_ctl` resolves to the same package object as idrac_ctl."""
    import idrac_ctl
    import redfish_ctl
    assert redfish_ctl is idrac_ctl


def test_redfish_ctl_submodule_is_same_object():
    """`from redfish_ctl.<sub> import X` returns the identical object as idrac_ctl's."""
    from redfish_ctl.redfish_manager import CommandResult as aliased

    from idrac_ctl.redfish_manager import CommandResult as real
    assert aliased is real


def test_env_prefers_redfish_over_idrac(monkeypatch):
    """REDFISH_* wins when both it and the legacy IDRAC_* are set."""
    monkeypatch.setenv("REDFISH_IP", "203.0.113.10")
    monkeypatch.setenv("IDRAC_IP", "198.51.100.10")
    assert _env("REDFISH_IP", "IDRAC_IP") == "203.0.113.10"


def test_env_falls_back_to_idrac(monkeypatch):
    """With REDFISH_* unset, the legacy IDRAC_* value is used."""
    monkeypatch.delenv("REDFISH_IP", raising=False)
    monkeypatch.setenv("IDRAC_IP", "198.51.100.20")
    assert _env("REDFISH_IP", "IDRAC_IP") == "198.51.100.20"


def test_env_default_when_none_set(monkeypatch):
    """Neither set -> the provided default."""
    monkeypatch.delenv("REDFISH_USERNAME", raising=False)
    monkeypatch.delenv("IDRAC_USERNAME", raising=False)
    assert _env("REDFISH_USERNAME", "IDRAC_USERNAME", default="root") == "root"
