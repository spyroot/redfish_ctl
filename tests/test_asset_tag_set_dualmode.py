"""Dual-mode tests for the asset-tag-set command."""

import pytest

from redfish_ctl.chassis.cmd_asset_tag_set import AssetTagSet
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_shared import ApiRequestType, RedfishApiRespond


def _request_type():
    request_type = getattr(ApiRequestType, "AssetTagSet", None)
    assert request_type is not None, "missing ApiRequestType.AssetTagSet"
    return request_type


def _mutating_requests(service):
    return [
        request
        for request in service.requests
        if request.method in {"POST", "PATCH", "DELETE"}
    ]


def _patch_requests(service):
    return [request for request in service.requests if request.method == "PATCH"]


def test_asset_tag_set_reads_current_chassis_tag_without_patch(
    redfish_mock_factory,
):
    """asset-tag-set reads the current chassis AssetTag when no tag is supplied."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        _request_type(),
        "asset-tag-set",
        resource="chassis",
        target_id="Chassis_0",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "resource": "chassis",
        "target_id": "Chassis_0",
        "target": "/redfish/v1/Chassis/Chassis_0",
        "current": "$PRODUCT_ASSET_TAG",
        "read_only": True,
    }
    assert _mutating_requests(service) == []


def test_asset_tag_set_dry_run_previews_system_patch_without_writing(
    redfish_mock_factory,
):
    """asset-tag-set previews the AssetTag PATCH unless confirmed."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        _request_type(),
        "asset-tag-set",
        resource="system",
        target_id="System_0",
        asset_tag="lab-system-01",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["current"] == ""
    assert result.data["payload"] == {"AssetTag": "lab-system-01"}
    assert result.data["target"] == "/redfish/v1/Systems/System_0"
    assert _mutating_requests(service) == []


def test_asset_tag_set_confirm_patches_and_rereads_chassis_tag(
    redfish_mock_factory,
):
    """asset-tag-set --confirm PATCHes only AssetTag and returns observed state."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        _request_type(),
        "asset-tag-set",
        resource="chassis",
        target_id="Chassis_0",
        asset_tag="restored-tag",
        confirm=True,
    )

    patches = _patch_requests(service)
    assert len(patches) == 1
    assert patches[0].path.lower() == result.data["applied"]["target"].lower()
    assert patches[0].json() == {"AssetTag": "restored-tag"}
    assert result.data["applied"] == {
        "target": "/redfish/v1/Chassis/Chassis_0",
        "status": "RedfishApiRespond.Ok",
        "error": None,
    }
    assert result.data["observed"] == "restored-tag"


def test_asset_tag_set_patch_error_does_not_reread_target(
    redfish_mock_factory,
    monkeypatch,
):
    """A failed PATCH is returned immediately instead of masking it with a GET."""
    manager, _service = redfish_mock_factory("supermicro")
    original_get = AssetTagSet._get
    state = {"get_count": 0, "patch_failed": False}

    def fail_patch(self, *args, **kwargs):
        state["patch_failed"] = True
        return (
            CommandResult({}, None, None, "PATCH failed"),
            RedfishApiRespond.Error,
        )

    def counted_get(self, uri, do_async):
        if state["patch_failed"]:
            raise AssertionError("unexpected re-read after failed PATCH")
        state["get_count"] += 1
        return original_get(self, uri, do_async)

    monkeypatch.setattr(AssetTagSet, "base_patch", fail_patch)
    monkeypatch.setattr(AssetTagSet, "_get", counted_get)

    result = manager.sync_invoke(
        _request_type(),
        "asset-tag-set",
        resource="chassis",
        target_id="Chassis_0",
        asset_tag="restored-tag",
        confirm=True,
    )

    assert result.error == "PATCH failed"
    assert result.data["applied"] == {
        "target": "/redfish/v1/Chassis/Chassis_0",
        "status": "RedfishApiRespond.Error",
        "error": "PATCH failed",
    }
    assert result.data["observed"] is None
    assert state["get_count"] > 0


def test_asset_tag_set_rejects_blank_target_without_requests(
    redfish_mock_factory,
):
    """A blank target id fails before any Redfish collection read."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument, match="target_id is required"):
        manager.sync_invoke(
            _request_type(),
            "asset-tag-set",
            resource="chassis",
            target_id="   ",
            asset_tag="restored-tag",
            confirm=True,
        )

    assert service.requests == []


def test_asset_tag_set_parser_rejects_blank_target_id():
    """The CLI parser rejects whitespace-only --target-id values."""
    parser, name, _help = AssetTagSet.register_subcommand(AssetTagSet)

    with pytest.raises(SystemExit):
        parser.parse_args(["--target-id", "   "])

    assert name == "asset-tag-set"


def test_asset_tag_set_allows_empty_restore_value(redfish_mock_factory):
    """An empty AssetTag is a valid restore value for vendors that start blank."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        _request_type(),
        "asset-tag-set",
        resource="system",
        target_id="System_0",
        asset_tag="",
    )

    assert result.data["payload"] == {"AssetTag": ""}
    assert result.data["current"] == ""
    assert _mutating_requests(service) == []


def test_asset_tag_set_rejects_missing_target_before_patch(redfish_mock_factory):
    """asset-tag-set fails closed when the target resource id is absent."""
    manager, service = redfish_mock_factory("supermicro")

    with pytest.raises(InvalidArgument, match="No chassis resource named Missing"):
        manager.sync_invoke(
            _request_type(),
            "asset-tag-set",
            resource="chassis",
            target_id="Missing",
            asset_tag="lab-system-01",
            confirm=True,
        )

    assert _mutating_requests(service) == []
