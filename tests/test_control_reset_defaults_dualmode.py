"""Dual-mode-style coverage for Control.ResetToDefaults."""

import json

from redfish_ctl.controls.cmd_control_reset_defaults import ControlResetDefaults
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def test_control_reset_defaults_lists_supermicro_targets_without_post(
        redfish_mock_factory):
    """Listing discovers reset-capable Controls and never POSTs."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.ControlResetDefaults,
        "control-reset-defaults",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    targets = result.data["control_reset_targets"]
    assert len(targets) == 4
    target_map = {(row["Chassis"], row["Id"]): row for row in targets}
    assert target_map[("HGX_GPU_0", "ClockLimit_0")] == {
        "Chassis": "HGX_GPU_0",
        "Id": "ClockLimit_0",
        "Name": "Control for GPU_0 ClockLimit_0",
        "ControlType": "FrequencyMHz",
        "ControlMode": "Automatic",
        "SetPoint": None,
        "SetPointUnits": "MHz",
        "DefaultSetPoint": None,
        "Uri": "/redfish/v1/Chassis/HGX_GPU_0/Controls/ClockLimit_0",
        "Target": (
            "/redfish/v1/Chassis/HGX_GPU_0/Controls/ClockLimit_0/"
            "Actions/Control.ResetToDefaults"
        ),
    }
    assert set(target_map) == {
        ("HGX_GPU_0", "ClockLimit_0"),
        ("HGX_GPU_1", "ClockLimit_0"),
        ("HGX_GPU_2", "ClockLimit_0"),
        ("HGX_GPU_3", "ClockLimit_0"),
    }
    assert _post_requests(service) == []


def test_control_reset_defaults_dry_runs_by_default(redfish_mock_factory):
    """A selected Control reset resolves the target but does not POST by default."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.ControlResetDefaults,
        "control-reset-defaults",
        control="HGX_GPU_0/ClockLimit_0",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["level"] == "destructive"
    assert result.data["payload"] == {}
    assert result.data["target"] == (
        "/redfish/v1/Chassis/HGX_GPU_0/Controls/ClockLimit_0/"
        "Actions/Control.ResetToDefaults"
    )
    assert _post_requests(service) == []


def test_control_reset_defaults_confirm_posts_selected_target(
        redfish_mock_factory):
    """--confirm POSTs exactly one selected Control reset action."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.ControlResetDefaults,
        "control-reset-defaults",
        control="ClockLimit_0",
        chassis="HGX_GPU_2",
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#Control.ResetToDefaults"
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/chassis/hgx_gpu_2/controls/clocklimit_0/"
        "actions/control.resettodefaults"
    )
    assert posts[0].json() == {}


def test_control_reset_defaults_ambiguous_selector_reports_without_post(
        redfish_mock_factory):
    """Duplicate Control ids require a chassis or URI selector."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.ControlResetDefaults,
        "control-reset-defaults",
        control="ClockLimit_0",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "multiple Control.ResetToDefaults targets found; "
        "pass --chassis or a full --control URI"
    )
    assert len(result.data["matches"]) == 4
    assert _post_requests(service) == []


def test_control_reset_defaults_missing_target_reports_without_post(
        redfish_mock_factory):
    """A fixture with no reset-capable Controls returns a structured error."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.ControlResetDefaults,
        "control-reset-defaults",
        control="ClockLimit_0",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == "Control.ResetToDefaults target not found: ClockLimit_0"
    assert result.data == {"available": []}
    assert _post_requests(service) == []


def test_control_reset_defaults_exposes_cli_entrypoint():
    """The control-reset-defaults command is wired into the package registry."""
    registry = RedfishManagerBase().get_registry()
    assert registry[ApiRequestType.ControlResetDefaults][
        "control-reset-defaults"
    ] is ControlResetDefaults

    cmd_parser, cmd_name, cmd_help = ControlResetDefaults.register_subcommand(
        ControlResetDefaults
    )

    assert "--control" in cmd_parser.format_help()
    assert "--confirm" in cmd_parser.format_help()
    assert cmd_name == "control-reset-defaults"
    assert "Control" in cmd_help
