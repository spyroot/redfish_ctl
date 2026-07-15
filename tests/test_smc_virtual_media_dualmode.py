"""Dual-mode tests for Supermicro OEM virtual-media mount commands."""

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def _requests(service, method):
    """Return recorded mock-service requests for one HTTP method."""
    return [request for request in service.requests if request.method == method]


def test_vm_mount_status_reads_supermicro_vm1_without_mutation(redfish_mock_factory):
    """vm-mount --status reads the Supermicro VM1 resource without writes."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.SmcVirtualMediaMount,
        "vm-mount",
        do_status=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["status"]["Members"] is None
    assert result.data["status"]["CD1"] is None
    assert [request.method for request in service.requests] == ["GET", "GET"]
    assert service.requests[0].path.lower() == "/redfish/v1/managers/1/vm1"
    assert service.requests[1].path.lower() == "/redfish/v1/managers/1/vm1/cd1"


def test_vm_mount_requires_share_host_and_path_without_writing(
    redfish_mock_factory,
):
    """vm-mount without a share host/path returns validation data only."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.SmcVirtualMediaMount,
        "vm-mount",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert "--host and --path are required" in result.data["error"]
    assert service.requests == []


def test_vm_mount_patches_cfgcd_then_posts_mount_action(redfish_mock_factory):
    """vm-mount writes the share config before posting the mount action."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.SmcVirtualMediaMount,
        "vm-mount",
        host="192.0.2.10",
        path="/isos/installer.iso",
        share_user="iso-user",
        share_pass="iso-pass",
    )

    patches = _requests(service, "PATCH")
    posts = _requests(service, "POST")
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["mounted"] is True
    assert result.data["host"] == "192.0.2.10"
    assert result.data["path"] == "/isos/installer.iso"
    assert len(patches) == 1
    assert patches[0].path.lower() == "/redfish/v1/managers/1/vm1/cfgcd"
    assert patches[0].json() == {
        "Host": "192.0.2.10",
        "Path": "/isos/installer.iso",
        "Username": "iso-user",
        "Password": "iso-pass",
    }
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/managers/1/vm1/cfgcd/actions/isoconfig.mount"
    )
    assert posts[0].json() == {}
    assert result.data["status"]["CD1"] is None


def test_vm_mount_unmount_posts_unmount_action(redfish_mock_factory):
    """vm-mount --unmount posts the Supermicro unmount action."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.SmcVirtualMediaMount,
        "vm-mount",
        do_unmount=True,
    )

    posts = _requests(service, "POST")
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["unmounted"] is True
    assert len(posts) == 1
    assert posts[0].path.lower() == (
        "/redfish/v1/managers/1/vm1/cfgcd/actions/isoconfig.unmount"
    )
    assert posts[0].json() == {}
    assert result.data["status"]["CD1"] is None
