"""Regression tests: failed writes must never be treated as success.

``base_post``/``base_patch`` return ``(CommandResult, RedfishApiRespond)``.
``RedfishApiRespond`` is an Enum, so ``api_resp.Success`` is attribute access
returning the class member — always truthy — NOT a comparison. Five commands
used that pattern, so an Error response satisfied the success branch; three
of them then escalated a failed PATCH into JobApply with a reboot. These
tests pin the correct behavior: an Error result returns the error to the
caller and never schedules a commit, while a real success still commits.

Each test monkeypatches the command's own write seam (``base_patch`` /
``base_post``) — no network, no live BMC.

Author Mus spyroot@gmail.com
"""
import json

from redfish_ctl.bios.cmd_change_bios import BiosChangeSettings
from redfish_ctl.bios.cmd_change_boot_order import ChangeBootOrder
from redfish_ctl.boot_source.cmd_update import BootSourceUpdate
from redfish_ctl.chassis.cmd_update_chassis import ChassisUpdate
from redfish_ctl.redfish_manager_shared import RedfishApiRespond
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.system.cmd_system_import import ImportSystemConfig

MOCK_SERVERS = "/redfish/v1/Systems/System.Embedded.1"


def _cmd(cls):
    """A command instance with mock credentials (offline; nothing contacted)."""
    return cls(
        idrac_ip="mock-idrac", idrac_username="root",
        idrac_password="mock", insecure=True, is_debug=False,
    )


def _error_write(*_args, **_kwargs):
    """A failed write: the base layer returns Error instead of raising."""
    return (
        CommandResult({"Status": "Failed"}, None, None, "mock redfish error"),
        RedfishApiRespond.Error,
    )


def _ok_write(*_args, **_kwargs):
    """A plain successful write with no task attached (HTTP 200/204)."""
    return CommandResult({}, None, None, None), RedfishApiRespond.Ok


def _spy_sync_invoke(record):
    """Record nested command invocations; serve empty results."""
    def spy(api_call, name, **kwargs):
        record.append(name)
        return CommandResult({}, None, None, None)
    return spy


def _write_spec(tmp_path, payload):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(payload))
    return str(spec)


def test_bios_change_failed_patch_does_not_commit(tmp_path, monkeypatch):
    """bios-change: a failed Settings PATCH with do_commit must not JobApply.

    This was live: RedfishApiRespond.Error satisfied ``api_resp.Success or
    api_resp.Ok`` (enum-member attribute access is always truthy), so a
    failed BIOS write logged "Commit changes and rebooting" and rebooted.
    """
    cmd = _cmd(BiosChangeSettings)
    invoked = []
    monkeypatch.setattr(cmd, "sync_invoke", _spy_sync_invoke(invoked))
    monkeypatch.setattr(
        cmd, "_resolve_bios_settings_uri", lambda *a, **k: f"{MOCK_SERVERS}/Bios/Settings"
    )
    monkeypatch.setattr(cmd, "base_patch", _error_write)

    spec = _write_spec(tmp_path, {"Attributes": {"ProcCStates": "Disabled"}})
    result = cmd.execute(from_spec=spec, do_commit=True)

    assert "job_apply" not in invoked
    assert result.error is not None


def test_bios_change_ok_patch_still_commits(tmp_path, monkeypatch):
    """bios-change: a real success with do_commit still schedules JobApply."""
    cmd = _cmd(BiosChangeSettings)
    invoked = []
    monkeypatch.setattr(cmd, "sync_invoke", _spy_sync_invoke(invoked))
    monkeypatch.setattr(
        cmd, "_resolve_bios_settings_uri", lambda *a, **k: f"{MOCK_SERVERS}/Bios/Settings"
    )
    monkeypatch.setattr(cmd, "base_patch", _ok_write)

    spec = _write_spec(tmp_path, {"Attributes": {"ProcCStates": "Disabled"}})
    cmd.execute(from_spec=spec, do_commit=True)

    assert "job_apply" in invoked


def test_boot_order_failed_patch_does_not_commit(tmp_path, monkeypatch):
    """boot-order change: a failed PATCH with do_commit must not JobApply."""
    cmd = _cmd(ChangeBootOrder)
    invoked = []
    cmd.__dict__["idrac_manage_servers"] = MOCK_SERVERS
    monkeypatch.setattr(cmd, "sync_invoke", _spy_sync_invoke(invoked))
    monkeypatch.setattr(cmd, "base_patch", _error_write)

    spec = _write_spec(tmp_path, {"Boot": {"BootOrder": ["HardDisk.List.1-1"]}})
    result = cmd.execute(boot_order="", from_spec=spec, do_commit=True)

    assert "job_apply" not in invoked
    assert result.error is not None


def test_boot_source_failed_patch_does_not_commit(tmp_path, monkeypatch):
    """boot-source update: a failed PATCH with do_commit must not JobApply."""
    cmd = _cmd(BootSourceUpdate)
    invoked = []
    cmd.__dict__["idrac_manage_servers"] = MOCK_SERVERS
    monkeypatch.setattr(cmd, "sync_invoke", _spy_sync_invoke(invoked))
    monkeypatch.setattr(cmd, "base_patch", _error_write)

    spec = _write_spec(tmp_path, {"BootSourceOverrideEnabled": "Once"})
    result = cmd.execute(from_spec=spec, do_commit=True)

    assert "job_apply" not in invoked
    assert result.error is not None


def test_chassis_update_plain_success_has_no_task(tmp_path, monkeypatch):
    """chassis update: a 200/204 with no task must not KeyError on task_id.

    ``api_resp.AcceptedTaskGenerated`` was always truthy, so every non-task
    response crashed reading ``data['task_id']`` from an empty dict.
    """
    cmd = _cmd(ChassisUpdate)
    monkeypatch.setattr(cmd, "base_patch", _ok_write)

    spec = _write_spec(tmp_path, {"AssetTag": "lab"})
    result = cmd.execute(chassis_id="System.Embedded.1", from_spec=spec)

    assert result.error is None
    assert "task_id" not in result.data


def test_system_import_task_reads_task_id_key(tmp_path, monkeypatch):
    """system-import: an accepted task exposes task_id (job_id never existed).

    The base layer stores the id under 'task_id'; this command read
    'job_id', so even the legitimate task path raised KeyError.
    """
    cmd = _cmd(ImportSystemConfig)

    def accepted_post(*_args, **_kwargs):
        return (
            CommandResult({"task_id": "JID_123"}, None, None, None),
            RedfishApiRespond.AcceptedTaskGenerated,
        )

    monkeypatch.setattr(cmd, "base_post", accepted_post)
    monkeypatch.setattr(cmd, "fetch_task", lambda *_a, **_k: "scheduled")

    config = tmp_path / "config.json"
    config.write_text("{}")
    result = cmd.execute(config=str(config))

    assert result.data["task_id"] == "JID_123"
    assert result.data["task_state"] == "scheduled"
