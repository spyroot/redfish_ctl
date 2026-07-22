"""Dual-mode tests for Dell VFlash partition actions."""
import copy
from pathlib import Path

import pytest
from conftest import MockRedfishService, _build_fixture_index
from vendor_corpus import corpus_dir

from redfish_ctl.actions.action_policy import Destructiveness, classify
from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.oem.cmd_dell_persistent_partition import (
    DellPersistentPartitionActions,
)
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[1]
DELL_CORPUS = corpus_dir(
    REPO_ROOT / "tests" / "dell_xr8620t_corpus.tar.gz",
    "10.252.252.209",
)
PERSISTENT_STORAGE_SERVICE = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DellPersistentStorageService"
)
ATTACH_TARGET = (
    f"{PERSISTENT_STORAGE_SERVICE}/Actions/"
    "DellPersistentStorageService.AttachPartition"
)
DELETE_TARGET = (
    f"{PERSISTENT_STORAGE_SERVICE}/Actions/"
    "DellPersistentStorageService.DeletePartition"
)


@pytest.fixture
def dell_persistent_mock():
    """Return a manager and mock service backed by the Dell XR8620t corpus.

    :return: tuple of Redfish manager and mock service.
    """
    requests_mock = pytest.importorskip("requests_mock")
    service = MockRedfishService(
        DELL_CORPUS,
        index=_build_fixture_index(DELL_CORPUS),
    )
    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=service.get_cb)
        mocker.patch(requests_mock.ANY, text=service.patch_cb)
        mocker.post(requests_mock.ANY, text=service.post_cb)
        mocker.delete(requests_mock.ANY, text=service.delete_cb)
        service.mocker = mocker
        yield (
            IDracManager(
                idrac_ip="mock-dell-persistent",
                idrac_username="root",
                idrac_password="mock",
                insecure=True,
                is_debug=False,
            ),
            service,
        )


def _post_requests(service):
    """Return POST requests recorded by the mock Redfish service.

    :param service: mock Redfish service.
    :return: recorded POST requests.
    """
    return [request for request in service.requests if request.method == "POST"]


def _overlay_persistent_service(service, body):
    """Overlay DellPersistentStorageService under both common request casings."""
    service._overlay[PERSISTENT_STORAGE_SERVICE] = body
    service._overlay[PERSISTENT_STORAGE_SERVICE.lower()] = body


def test_dell_vflash_partition_lists_corpus_actions_without_post(dell_persistent_mock):
    """Listing discovers partition actions and never POSTs."""
    manager, service = dell_persistent_mock

    result = manager.sync_invoke(
        ApiRequestType.DellPersistentPartitionActions,
        "dell-vflash-partition",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["persistent_storage_service"] == PERSISTENT_STORAGE_SERVICE
    actions = {row["Action"]: row for row in result.data["partition_actions"]}
    assert set(actions) == {
        "attach",
        "create",
        "create-from-image",
        "delete",
        "detach",
        "export",
        "format",
        "initialize",
        "modify",
    }
    assert actions["create-from-image"]["AllowableValues"]["ShareType"] == [
        "CIFS",
        "FTP",
        "HTTP",
        "NFS",
        "TFTP",
    ]
    assert actions["format"]["Level"] == "irreversible"
    assert _post_requests(service) == []


def test_dell_vflash_partition_attach_defaults_to_dry_run(dell_persistent_mock):
    """Attach resolves the Dell target but does not POST without --confirm."""
    manager, service = dell_persistent_mock

    result = manager.sync_invoke(
        ApiRequestType.DellPersistentPartitionActions,
        "dell-vflash-partition",
        action="attach",
        partition_index=2,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellPersistentStorageService.AttachPartition"
    assert result.data["target"] == ATTACH_TARGET
    assert result.data["level"] == "destructive"
    assert result.data["blocked"] == "destructive action requires --confirm"
    assert result.data["payload"] == {"PartitionIndex": 2}
    assert _post_requests(service) == []


def test_dell_vflash_partition_attach_confirm_posts_payload(dell_persistent_mock):
    """--confirm POSTs one AttachPartition request to the discovered target."""
    manager, service = dell_persistent_mock

    result = manager.sync_invoke(
        ApiRequestType.DellPersistentPartitionActions,
        "dell-vflash-partition",
        action="attach",
        partition_index=2,
        confirm=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "destructive"
    assert len(posts) == 1
    assert posts[0].path.lower() == ATTACH_TARGET.lower()
    assert posts[0].json() == {"PartitionIndex": 2}


def test_dell_vflash_partition_delete_needs_irreversible_token(dell_persistent_mock):
    """DeletePartition dry-runs until both confirmation flags are supplied."""
    manager, service = dell_persistent_mock

    result = manager.sync_invoke(
        ApiRequestType.DellPersistentPartitionActions,
        "dell-vflash-partition",
        action="delete",
        partition_index=3,
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["action"] == "#DellPersistentStorageService.DeletePartition"
    assert result.data["target"] == DELETE_TARGET
    assert result.data["level"] == "irreversible"
    assert result.data["blocked"] == (
        "irreversible action requires --confirm and --i-understand-irreversible"
    )
    assert _post_requests(service) == []


def test_dell_vflash_partition_delete_with_both_tokens_posts(dell_persistent_mock):
    """DeletePartition POSTs only after both confirmation flags are supplied."""
    manager, service = dell_persistent_mock

    result = manager.sync_invoke(
        ApiRequestType.DellPersistentPartitionActions,
        "dell-vflash-partition",
        action="delete",
        partition_index=3,
        confirm=True,
        confirm_irreversible=True,
    )

    posts = _post_requests(service)
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["executed"] is True
    assert result.data["level"] == "irreversible"
    assert len(posts) == 1
    assert posts[0].path.lower() == DELETE_TARGET.lower()
    assert posts[0].json() == {"PartitionIndex": 3}


def test_dell_vflash_partition_rejects_invalid_allowable_value(dell_persistent_mock):
    """Inline Dell allowable values reject unsupported CreatePartition input."""
    manager, service = dell_persistent_mock

    result = manager.sync_invoke(
        ApiRequestType.DellPersistentPartitionActions,
        "dell-vflash-partition",
        action="create",
        partition_index=1,
        partition_type="Tape",
        size=1,
        size_unit="GB",
        os_volume_label="BOOT",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "invalid value for DellPersistentStorageService.CreatePartition "
        "PartitionType: Tape; allowed: Floppy, HardDisk"
    )
    assert result.data["validation_errors"] == [
        {
            "parameter": "PartitionType",
            "value": "Tape",
            "allowed": ["Floppy", "HardDisk"],
        }
    ]
    assert _post_requests(service) == []


def test_dell_vflash_partition_redacts_share_password(dell_persistent_mock, monkeypatch):
    """Dry-run output does not echo a share password read from the environment."""
    manager, service = dell_persistent_mock
    monkeypatch.setenv("DELL_VFLASH_PASSWORD", "placeholder-value")

    result = manager.sync_invoke(
        ApiRequestType.DellPersistentPartitionActions,
        "dell-vflash-partition",
        action="export",
        partition_index=1,
        share_type="CIFS",
        image_name="partition.img",
        share_username="share-user",
        password_env="DELL_VFLASH_PASSWORD",
        share_port=445,
        dry_run=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["payload"]["Username"] == "share-user"
    assert result.data["payload"]["Password"] == "********"
    assert result.data["payload"]["Port"] == 445
    assert result.data["payload"]["ShareType"] == "CIFS"
    assert _post_requests(service) == []


def test_dell_vflash_partition_reports_missing_action_without_post(dell_persistent_mock):
    """A service missing the selected action reports available actions."""
    manager, service = dell_persistent_mock
    body = copy.deepcopy(service._state(PERSISTENT_STORAGE_SERVICE))
    body["Actions"].pop("#DellPersistentStorageService.AttachPartition")
    _overlay_persistent_service(service, body)

    result = manager.sync_invoke(
        ApiRequestType.DellPersistentPartitionActions,
        "dell-vflash-partition",
        action="attach",
        partition_index=1,
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error == (
        "Dell persistent-storage partition action not found: attach"
    )
    available = {row["Action"] for row in result.data["available"]}
    assert "attach" not in available
    assert "delete" in available
    assert _post_requests(service) == []


def test_dell_vflash_partition_rejects_out_of_range_index(dell_persistent_mock):
    """Partition index validation rejects values outside Dell's 1-16 range."""
    manager, service = dell_persistent_mock

    with pytest.raises(
        InvalidArgument,
        match="--partition-index must be between 1 and 16",
    ):
        manager.sync_invoke(
            ApiRequestType.DellPersistentPartitionActions,
            "dell-vflash-partition",
            action="attach",
            partition_index=17,
            confirm=True,
        )

    assert _post_requests(service) == []


def test_dell_vflash_partition_is_registered_and_classified():
    """The command is registered and Dell partition actions are classified."""
    registry = IDracManager().get_registry()
    assert registry[ApiRequestType.DellPersistentPartitionActions][
        "dell-vflash-partition"
    ] is DellPersistentPartitionActions

    assert classify("#DellPersistentStorageService.AttachPartition") is (
        Destructiveness.DESTRUCTIVE
    )
    assert classify("#DellPersistentStorageService.DeletePartition") is (
        Destructiveness.IRREVERSIBLE
    )
    assert classify("#DellPersistentStorageService.FormatPartition") is (
        Destructiveness.IRREVERSIBLE
    )

    cmd_parser, cmd_name, cmd_help = (
        DellPersistentPartitionActions.register_subcommand(
            DellPersistentPartitionActions
        )
    )
    help_text = cmd_parser.format_help()

    assert cmd_name == "dell-vflash-partition"
    assert "VFlash" in cmd_help
    assert "--action" in help_text
    assert "--i-understand-irreversible" in help_text
