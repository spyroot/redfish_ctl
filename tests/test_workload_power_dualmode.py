"""Dual-mode tests for guarded NVIDIA WorkloadPower profile actions."""

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.command_shared import ApiRequestType


def _mutating_requests(service):
    return [
        request for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]


def test_workload_power_enable_dry_run_resolves_gb300_target_without_post(
        redfish_mock_factory):
    """workload-power previews the GPU action target and sends no POST by default."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.WorkloadPower,
        "workload-power",
        gpu_id="GPU_0",
        profile_mask="0x1",
        mode="enable",
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["gpu"] == "GPU_0"
    assert result.data["resource"] == (
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
        "Nvidia/WorkloadPowerProfile"
    )
    assert result.data["action"] == "#NvidiaWorkloadPower.EnableProfiles"
    assert result.data["target"] == (
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/Nvidia/"
        "WorkloadPowerProfile/Actions/NvidiaWorkloadPower.EnableProfiles"
    )
    assert result.data["payload"] == {"ProfileMask": "0x1"}
    assert _mutating_requests(service) == []


def test_workload_power_disable_confirm_posts_profile_mask_to_discovered_action(
        redfish_mock_factory):
    """workload-power --confirm POSTs only the requested profile-mask action."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.WorkloadPower,
        "workload-power",
        gpu_id="GPU_0",
        profile_mask="0x1",
        mode="disable",
        confirm=True,
    )

    posts = [request for request in service.requests if request.method == "POST"]
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#NvidiaWorkloadPower.DisableProfiles"
    assert len(posts) == 1
    assert posts[0].path == (
        "/redfish/v1/systems/hgx_baseboard_0/processors/gpu_0/oem/nvidia/"
        "workloadpowerprofile/actions/nvidiaworkloadpower.disableprofiles"
    )
    assert posts[0].json() == {"ProfileMask": "0x1"}


def test_workload_power_rejects_invalid_profile_mask_before_post(
        redfish_mock_factory):
    """The profile mask must be an explicit Redfish hex mask before any write."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument):
        manager.sync_invoke(
            ApiRequestType.WorkloadPower,
            "workload-power",
            gpu_id="GPU_0",
            profile_mask="1",
            mode="enable",
            confirm=True,
        )

    assert _mutating_requests(service) == []
