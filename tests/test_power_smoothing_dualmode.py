"""Offline coverage for the GB300 NVIDIA PowerSmoothing reader."""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.redfish_manager_base import RedfishManagerBase
from redfish_ctl.redfish_manager_shared import ApiRequestType

GB300_CORPUS = corpus_dir(
    Path(__file__).parent / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
)
GB300_INDEX = {path.name.lower(): path for path in GB300_CORPUS.glob("*.json")}


def _fixture_for_path(path):
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return GB300_INDEX.get(name.lower())


def _mutating_requests(service):
    return [
        request for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]


@pytest.fixture
def gb300_corpus_manager():
    """Serve the committed GB300 crawl over requests-mock."""
    requests_mock = pytest.importorskip("requests_mock")
    requests = []

    def get_cb(request, context):
        requests.append(request)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text()

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        manager = RedfishManagerBase(
            idrac_ip="mock-gb300",
            idrac_username="root",
            idrac_password="mock",
            insecure=True,
            is_debug=False,
        )
        yield manager, requests


def test_power_smoothing_reads_gb300_gpu_profiles_and_setpoints(
    gb300_corpus_manager,
):
    """power-smoothing walks GB300 GPU OEM links without writes."""
    manager, requests = gb300_corpus_manager

    result = manager.sync_invoke(
        ApiRequestType.PowerSmoothing,
        "power-smoothing",
    )

    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    assert result.data["summary"] == {
        "systems": 2,
        "gpu_processors": 4,
        "power_smoothing_resources": 4,
        "supported": 4,
        "enabled": 0,
        "preset_collections": 4,
        "preset_profiles": 12,
        "admin_override_profiles": 4,
    }

    resources = {row["GPU"]: row for row in result.data["power_smoothing"]}
    assert resources["GPU_0"] == {
        "System": "HGX_Baseboard_0",
        "GPU": "GPU_0",
        "Name": "GPU_0 Power Smoothing",
        "Enabled": False,
        "PowerSmoothingSupported": True,
        "ImmediateRampDown": False,
        "RampDownHysteresisSeconds": 10.0,
        "RampDownWattsPerSecond": 20.0,
        "RampUpWattsPerSecond": 20.0,
        "TMPFloorPercent": 89.990234375,
        "TMPFloorWatts": 0.0,
        "TMPWatts": 1459.0,
        "RemainingLifetimeCircuitryPercent": 100.0,
        "AppliedPresetProfileUri": (
            "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
            "Nvidia/PowerSmoothing/PresetProfiles/0"
        ),
        "AdminOverrideProfileUri": (
            "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
            "Nvidia/PowerSmoothing/AdminOverrideProfile"
        ),
        "PresetProfilesUri": (
            "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
            "Nvidia/PowerSmoothing/PresetProfiles"
        ),
        "ActivatePresetProfileTarget": (
            "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
            "Nvidia/PowerSmoothing/Actions/"
            "NvidiaPowerSmoothing.ActivatePresetProfile"
        ),
        "ApplyAdminOverridesTarget": (
            "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
            "Nvidia/PowerSmoothing/Actions/"
            "NvidiaPowerSmoothing.ApplyAdminOverrides"
        ),
        "Uri": (
            "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
            "Nvidia/PowerSmoothing"
        ),
    }

    collections = {
        row["GPU"]: row
        for row in result.data["preset_collections"]
    }
    assert collections["GPU_0"] == {
        "System": "HGX_Baseboard_0",
        "GPU": "GPU_0",
        "Name": "GPU_0 PowerSmoothing PresetProfile Collection",
        "MemberCount": 5,
        "Uri": (
            "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
            "Nvidia/PowerSmoothing/PresetProfiles"
        ),
    }

    profiles = {
        (row["GPU"], row["Id"]): row
        for row in result.data["preset_profiles"]
    }
    assert profiles[("GPU_0", "0")] == {
        "System": "HGX_Baseboard_0",
        "GPU": "GPU_0",
        "Id": "0",
        "Name": "GPU_0 PowerSmoothing PresetProfile 0",
        "RampDownHysteresisSeconds": 10.0,
        "RampDownWattsPerSecond": 20.0,
        "RampUpWattsPerSecond": 20.0,
        "TMPFloorPercent": 89.990234375,
        "Uri": (
            "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
            "Nvidia/PowerSmoothing/PresetProfiles/0"
        ),
    }

    admin_profiles = {
        row["GPU"]: row
        for row in result.data["admin_override_profiles"]
    }
    assert admin_profiles["GPU_0"] == {
        "System": "HGX_Baseboard_0",
        "GPU": "GPU_0",
        "Id": "AdminOverrideProfile",
        "Name": "GPU_0 PowerSmoothing AdminOverrideProfile",
        "RampDownHysteresisSeconds": 4294967295.0,
        "RampDownWattsPerSecond": 4294967295.0,
        "RampUpWattsPerSecond": 4294967295.0,
        "TMPFloorPercent": 4294967295.0,
        "Uri": (
            "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
            "Nvidia/PowerSmoothing/AdminOverrideProfile"
        ),
    }

    paths = {request.path.lower() for request in requests}
    assert (
        "/redfish/v1/systems/hgx_baseboard_0/processors/gpu_0/"
        "oem/nvidia/powersmoothing"
        in paths
    )
    assert {
        request.method
        for request in requests
        if request.method in {"POST", "PATCH", "DELETE"}
    } == set()


def test_power_smoothing_action_apply_admin_dry_run_resolves_without_post(
        redfish_mock_factory):
    """power-smoothing-action previews ApplyAdminOverrides by default."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.PowerSmoothingAction,
        "power-smoothing-action",
        gpu_id="GPU_0",
        mode="apply-admin",
    )

    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#NvidiaPowerSmoothing.ApplyAdminOverrides"
    assert result.data["gpu"] == "GPU_0"
    assert result.data["resource"] == (
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
        "Nvidia/PowerSmoothing"
    )
    assert result.data["target"] == (
        "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/Nvidia/"
        "PowerSmoothing/Actions/NvidiaPowerSmoothing.ApplyAdminOverrides"
    )
    assert result.data["payload"] == {}
    assert _mutating_requests(service) == []


def test_power_smoothing_action_activate_confirm_posts_preset_link(
        redfish_mock_factory):
    """power-smoothing-action --confirm activates the requested preset profile."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        ApiRequestType.PowerSmoothingAction,
        "power-smoothing-action",
        gpu_id="GPU_0",
        mode="activate-preset",
        preset_profile="0",
        confirm=True,
    )

    posts = [request for request in service.requests if request.method == "POST"]
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["action"] == "#NvidiaPowerSmoothing.ActivatePresetProfile"
    assert result.data["mode"] == "activate-preset"
    assert len(posts) == 1
    assert posts[0].path == (
        "/redfish/v1/systems/hgx_baseboard_0/processors/gpu_0/oem/nvidia/"
        "powersmoothing/actions/nvidiapowersmoothing.activatepresetprofile"
    )
    assert posts[0].json() == {
        "PresetProfile": {
            "@odata.id": (
                "/redfish/v1/Systems/HGX_Baseboard_0/Processors/GPU_0/Oem/"
                "Nvidia/PowerSmoothing/PresetProfiles/0"
            )
        }
    }


def test_power_smoothing_action_requires_preset_before_post(
        redfish_mock_factory):
    """activate-preset refuses to run without an explicit profile selector."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument):
        manager.sync_invoke(
            ApiRequestType.PowerSmoothingAction,
            "power-smoothing-action",
            gpu_id="GPU_0",
            mode="activate-preset",
            confirm=True,
        )

    assert _mutating_requests(service) == []
