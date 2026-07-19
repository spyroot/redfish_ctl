"""Dual-mode-style coverage for Control.ResetToDefaults."""

import json

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.controls.cmd_control_reset_defaults import ControlResetDefaults
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType
from test_roundtrip_budget import projected_walltime

_CONTROL_URI = "/redfish/v1/Chassis/HGX_GPU_0/Controls/ClockLimit_0"
_CONTROL_TARGET = f"{_CONTROL_URI}/Actions/Control.ResetToDefaults"


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def _get_requests(service):
    """Return GET requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded GET requests.
    """
    return [request for request in service.requests if request.method == "GET"]


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
        "Uri": _CONTROL_URI,
        "Target": _CONTROL_TARGET,
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
    assert result.data["target"] == _CONTROL_TARGET
    assert result.data["Uri"] == _CONTROL_URI
    assert result.data["Target"] == _CONTROL_TARGET
    assert result.data["SetPoint"] is None
    assert result.data["DefaultSetPoint"] is None
    assert _post_requests(service) == []


def test_control_reset_defaults_exact_uri_skips_full_chassis_crawl(
        redfish_mock_factory):
    """An exact Control URI fetches only the selected Control before preview."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.ControlResetDefaults,
        "control-reset-defaults",
        control=_CONTROL_URI,
    )

    get_paths = [request.path.lower() for request in _get_requests(service)]
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["target"] == _CONTROL_TARGET
    assert get_paths.count(_CONTROL_URI.lower()) == 2
    assert "/redfish/v1/chassis" not in get_paths
    assert projected_walltime(service, "india-vpn-to-us") <= 0.61
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
    assert result.data["Chassis"] == "HGX_GPU_2"
    assert result.data["Id"] == "ClockLimit_0"
    assert result.data["ControlMode"] == "Automatic"
    assert result.data["SetPoint"] is None
    assert result.data["SetPointUnits"] == "MHz"
    assert result.data["DefaultSetPoint"] is None
    assert result.data["Uri"] == "/redfish/v1/Chassis/HGX_GPU_2/Controls/ClockLimit_0"
    assert result.data["Target"] == (
        "/redfish/v1/Chassis/HGX_GPU_2/Controls/ClockLimit_0/"
        "Actions/Control.ResetToDefaults"
    )
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/chassis/hgx_gpu_2/controls/clocklimit_0/"
        "actions/control.resettodefaults"
    )
    assert posts[0].json() == {}


def test_control_reset_defaults_confirm_dry_run_still_does_not_post(
        redfish_mock_factory):
    """--dry_run wins over --confirm and leaves the BMC untouched."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.ControlResetDefaults,
        "control-reset-defaults",
        control=_CONTROL_URI,
        confirm=True,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["blocked"] is None
    assert result.data["Uri"] == _CONTROL_URI
    assert _post_requests(service) == []


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


@pytest.mark.parametrize("unsafe_uri", [
    "/redfish/v1/Managers/BMC_0/Actions/Manager.Reset",
    "/redfish/v1/Managers/BMC_0/Actions/Manager.ResetToDefaults",
    "/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset",
    "/redfish/v1/Chassis/HGX_GPU_0/Actions/Chassis.Reset",
])
def test_control_reset_defaults_rejects_non_control_reset_uris_without_post(
        redfish_mock_factory, unsafe_uri):
    """Only Control.ResetToDefaults action URIs are accepted."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument, match="Control"):
        manager.sync_invoke(
            ApiRequestType.ControlResetDefaults,
            "control-reset-defaults",
            control=unsafe_uri,
            confirm=True,
        )

    assert _post_requests(service) == []


def test_control_reset_defaults_chassis_requires_control(redfish_mock_factory):
    """--chassis without --control is rejected instead of silently listing."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument, match="--chassis requires --control"):
        manager.sync_invoke(
            ApiRequestType.ControlResetDefaults,
            "control-reset-defaults",
            chassis="HGX_GPU_0",
        )

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
