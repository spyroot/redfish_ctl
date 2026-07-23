"""Dual-mode tests for Dell NetworkDeviceFunction attribute settings."""
import json
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz", "10.252.252.209"
)


@pytest.fixture
def dell_corpus_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus.

    :return: tuple of Redfish manager and mock service.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(DELL_CORPUS, index=_build_fixture_index(DELL_CORPUS))
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-xr8620t",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _patch_requests(service):
    """Return PATCH requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded PATCH requests.
    """
    return [request for request in service.requests if request.method == "PATCH"]


def test_dell_network_attributes_lists_corpus_targets_without_patch(dell_corpus_mock):
    """Listing discovers DellNetworkAttributes settings targets from corpus links."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellNetworkAttributes,
        "dell-network-attributes",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert len(result.data) == 8
    row = {item["Function"]: item for item in result.data}["NIC.Slot.2-1-1"]
    assert row["Adapter"] == "NIC.Slot.2"
    assert row["Attributes"] == (
        "/redfish/v1/Chassis/System.Embedded.1/NetworkAdapters/NIC.Slot.2/"
        "NetworkDeviceFunctions/NIC.Slot.2-1-1/Oem/Dell/"
        "DellNetworkAttributes/NIC.Slot.2-1-1"
    )
    assert row["Settings"] == f"{row['Attributes']}/Settings"
    assert row["AttributeRegistry"] == "NetworkAttributeRegistry_NIC.Slot.2-1-1"
    assert row["AttributeCount"] > 50
    assert "Immediate" in row["SupportedApplyTimes"]
    assert _patch_requests(service) == []


def test_dell_network_attributes_reads_one_target_attributes(dell_corpus_mock):
    """A target-id without a spec returns current and pending attributes."""
    manager, service = dell_corpus_mock

    result = manager.sync_invoke(
        ApiRequestType.DellNetworkAttributes,
        "dell-network-attributes",
        target_id="NIC.Slot.2-1-1",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["read_only"] is True
    assert result.data["CurrentAttributes"]["VLanId"] == 0
    assert result.data["CurrentAttributes"]["LegacyBootProto"] == "PXE"
    assert result.data["PendingAttributes"] == {}
    assert _patch_requests(service) == []


def test_dell_network_attributes_dry_run_previews_settings_patch(
    dell_corpus_mock, tmp_path
):
    """A spec previews by default and does not PATCH the settings resource."""
    manager, service = dell_corpus_mock
    spec = tmp_path / "dell-network-attributes.json"
    spec.write_text(json.dumps({"Attributes": {"VLanId": 101}}))

    result = manager.sync_invoke(
        ApiRequestType.DellNetworkAttributes,
        "dell-network-attributes",
        target_id="NIC.Slot.2-1-1",
        from_spec=str(spec),
        apply_time="OnReset",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["requires_confirm"] is True
    assert result.data["payload"] == {
        "Attributes": {"VLanId": 101},
        "@Redfish.SettingsApplyTime": {"ApplyTime": "OnReset"},
    }
    assert _patch_requests(service) == []


def test_dell_network_attributes_confirm_patches_settings_target(
    dell_corpus_mock, tmp_path
):
    """--confirm PATCHes only the discovered DellNetworkAttributes Settings URI."""
    manager, service = dell_corpus_mock
    spec = tmp_path / "dell-network-attributes.json"
    spec.write_text(json.dumps({"Attributes": {"VLanId": 101}}))

    result = manager.sync_invoke(
        ApiRequestType.DellNetworkAttributes,
        "dell-network-attributes",
        target_id="NIC.Slot.2-1-1",
        from_spec=str(spec),
        confirm=True,
    )

    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].path.lower() == result.data["Settings"].lower()
    assert patches[0].json() == {"Attributes": {"VLanId": 101}}
    assert result.error is None
    assert result.data["applied"]["target"] == result.data["Settings"]
    assert result.data["applied"]["error"] is None
    assert result.data["observed"] == {"VLanId": 101}


def test_dell_network_attributes_rejects_unknown_attribute_before_patch(
    dell_corpus_mock, tmp_path
):
    """Unknown attributes are rejected before any PATCH is sent."""
    manager, service = dell_corpus_mock
    spec = tmp_path / "bad-dell-network-attributes.json"
    spec.write_text(json.dumps({"Attributes": {"NoSuchNetworkAttribute": "Enabled"}}))

    with pytest.raises(InvalidArgument, match="unknown Dell network attribute"):
        manager.sync_invoke(
            ApiRequestType.DellNetworkAttributes,
            "dell-network-attributes",
            target_id="NIC.Slot.2-1-1",
            from_spec=str(spec),
            confirm=True,
        )

    assert _patch_requests(service) == []
