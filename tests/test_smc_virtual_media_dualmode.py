"""Dual-mode tests for Supermicro OEM virtual-media mount commands."""

from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.idrac_shared import ApiRequestType


def _requests(service, method):
    """Return recorded mock-service requests for one HTTP method."""
    return [request for request in service.requests if request.method == method]


def test_vm_mount_status_reads_supermicro_vm1_without_mutation(redfish_mock_factory):
    """vm-mount --status reports read diagnostics when X10 slots are absent."""
    manager, service = redfish_mock_factory("supermicro_x10")

    result = manager.sync_invoke(
        ApiRequestType.SmcVirtualMediaMount,
        "vm-mount",
        do_status=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    status = result.data["status"]
    assert status["Members"] is None
    assert status["VirtualMediaConfig"] == "/redfish/v1/Managers/1/VM1/CfgCD"
    assert status["CfgCD"] is None
    assert status["CD1"] is None
    assert status["reads"]["VM1"] == {
        "path": "/redfish/v1/Managers/1/VM1",
        "status": 200,
        "ok": True,
    }
    assert status["reads"]["CfgCD"] == {
        "path": "/redfish/v1/Managers/1/VM1/CfgCD",
        "status": 404,
        "ok": False,
        "error": "HTTP 404",
    }
    assert status["reads"]["CD1"] == {
        "path": "/redfish/v1/Managers/1/VM1/CD1",
        "status": 404,
        "ok": False,
        "error": "HTTP 404",
    }
    assert [request.method for request in service.requests] == ["GET", "GET", "GET"]
    assert service.requests[0].path.lower() == "/redfish/v1/managers/1/vm1"
    assert service.requests[1].path.lower() == "/redfish/v1/managers/1/vm1/cfgcd"
    assert service.requests[2].path.lower() == "/redfish/v1/managers/1/vm1/cd1"


def test_vm_mount_status_reads_cfgcd_and_cd1_when_available(redfish_mock_factory):
    """vm-mount --status reports useful CfgCD/CD1 readback without writes."""
    manager, service = redfish_mock_factory("supermicro_x10")
    service._overlay["/redfish/v1/managers/1/vm1/cfgcd"] = {
        "Host": "192.0.2.10",
        "Path": "/redfish-iso/installer.iso",
        "Username": "iso-user",
        "Password": "secret",
        "ShareType": "CIFS",
    }
    service._overlay["/redfish/v1/managers/1/vm1/cd1"] = {
        "Inserted": True,
        "ImageName": "installer.iso",
        "Image": "//192.0.2.10/redfish-iso/installer.iso",
        "MediaTypes": ["CD", "DVD"],
    }

    result = manager.sync_invoke(
        ApiRequestType.SmcVirtualMediaMount,
        "vm-mount",
        do_status=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    status = result.data["status"]
    assert status["CfgCD"] == {
        "Host": "192.0.2.10",
        "Path": "/redfish-iso/installer.iso",
        "Username": "iso-user",
        "ShareType": "CIFS",
    }
    assert "Password" not in status["CfgCD"]
    assert status["CD1"] == {
        "Inserted": True,
        "ImageName": "installer.iso",
        "Image": "//192.0.2.10/redfish-iso/installer.iso",
        "MediaTypes": ["CD", "DVD"],
    }
    assert status["reads"]["CfgCD"]["ok"] is True
    assert status["reads"]["CD1"]["ok"] is True
    assert [request.method for request in service.requests] == ["GET", "GET", "GET"]


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
    assert result.data["status"]["CfgCD"] == {
        "Host": "192.0.2.10",
        "Path": "/isos/installer.iso",
        "Username": "iso-user",
    }
    assert "Password" not in result.data["status"]["CfgCD"]
    assert result.data["status"]["CD1"] is None
    assert result.data["status"]["reads"]["CD1"]["error"] == "HTTP 404"


def test_vm_mount_uses_advertised_cfgcd_path(redfish_mock_factory):
    """vm-mount writes to the CfgCD link advertised by the VM1 resource."""
    manager, service = redfish_mock_factory("supermicro_x10")
    service._overlay["/redfish/v1/managers/1/vm1"] = {
        "Oem": {
            "Supermicro": {
                "VirtualMediaConfig": {
                    "@odata.id": "/redfish/v1/Managers/1/VM1/ConfigCD"
                }
            }
        }
    }
    service._overlay["/redfish/v1/managers/1/vm1/configcd"] = {
        "Host": "192.0.2.10",
        "Path": "/isos/installer.iso",
        "Username": "iso-user",
    }

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
    assert patches[0].path.lower() == "/redfish/v1/managers/1/vm1/configcd"
    assert posts[0].path.lower() == (
        "/redfish/v1/managers/1/vm1/configcd/actions/isoconfig.mount"
    )
    assert result.data["status"]["VirtualMediaConfig"] == (
        "/redfish/v1/Managers/1/VM1/ConfigCD"
    )


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


def test_vm_mount_unmount_uses_advertised_cfgcd_path(redfish_mock_factory):
    """vm-mount --unmount follows the advertised CfgCD action path."""
    manager, service = redfish_mock_factory("supermicro_x10")
    service._overlay["/redfish/v1/managers/1/vm1"] = {
        "Oem": {
            "Supermicro": {
                "VirtualMediaConfig": {
                    "@odata.id": "/redfish/v1/Managers/1/VM1/ConfigCD"
                }
            }
        }
    }
    service._overlay["/redfish/v1/managers/1/vm1/configcd"] = {}

    result = manager.sync_invoke(
        ApiRequestType.SmcVirtualMediaMount,
        "vm-mount",
        do_unmount=True,
    )

    posts = _requests(service, "POST")
    assert isinstance(result, CommandResult)
    assert result.error is None
    assert posts[0].path.lower() == (
        "/redfish/v1/managers/1/vm1/configcd/actions/isoconfig.unmount"
    )
