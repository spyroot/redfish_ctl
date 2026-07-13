"""redfish_ctl package import and REDFISH_* env helpers."""

from redfish_ctl.redfish_main import _env


def test_redfish_ctl_imports_real_package():
    """`import redfish_ctl` resolves to the source package."""
    import redfish_ctl

    assert redfish_ctl.__name__ == "redfish_ctl"


def test_redfish_ctl_submodule_imports():
    """Submodule imports resolve through the canonical package name."""
    from redfish_ctl.redfish_manager import CommandResult

    assert CommandResult.__name__ == "cmd_result"


def test_env_reads_redfish_name(monkeypatch):
    """Endpoint helpers read the canonical REDFISH_* name."""
    monkeypatch.setenv("REDFISH_IP", "203.0.113.10")
    assert _env("REDFISH_IP") == "203.0.113.10"


def test_env_default_when_none_set(monkeypatch):
    """Neither set -> the provided default."""
    monkeypatch.delenv("REDFISH_USERNAME", raising=False)
    assert _env("REDFISH_USERNAME", default="root") == "root"
