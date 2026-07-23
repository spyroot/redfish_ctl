"""Dual-mode tests for the ``mount_cdrom`` DMTF InsertMedia command.

``mount_cdrom`` (``MountCdrom``, scm_type ``VirtualMediaInsert``) is a thin
CD-ROM front end over the standard Redfish ``VirtualMedia.InsertMedia`` action.
These tests assert vendor-faithful task realization against BOTH a Dell tree
(``redfish_mock`` -> a ``JID_`` OEM job id) and a Supermicro tree
(``redfish_mock_factory("supermicro")`` -> a DMTF ``TaskService`` id), plus the
domain-error boundary for a missing image URI.
"""

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_mount_cdrom_requires_uri_path(redfish_api):
    """A missing --uri_path returns a domain error, never raising an exception."""
    result = redfish_api.sync_invoke(
        ApiRequestType.VirtualMediaInsert,
        "mount_cdrom",
    )

    assert isinstance(result, CommandResult)
    assert result.error is not None
    assert "URI" in result.error


def test_mount_cdrom_dell_posts_insertmedia_and_realizes_dell_job(
    redfish_mock, redfish_service, monkeypatch
):
    """Dell mount_cdrom POSTs InsertMedia and realizes a Dell JID_ job id."""
    monkeypatch.setattr(
        IDracManager,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )

    result = redfish_mock.sync_invoke(
        ApiRequestType.VirtualMediaInsert,
        "mount_cdrom",
        uri_path="http://example.test/rhel.iso",
        device_id="1",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    # Vendor-faithful realization: the Dell mock hands back a JID_ job id.
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert redfish_service.JOB_ID.startswith("JID_")
    assert result.data["task_state"] == {"TaskState": "Completed"}
    assert redfish_service.last_request.path == (
        "/redfish/v1/systems/system.embedded.1/virtualmedia/1/"
        "actions/virtualmedia.insertmedia"
    )
    assert redfish_service.last_request.json() == {
        "Image": "http://example.test/rhel.iso",
        "Inserted": True,
        "WriteProtected": True,
    }


def test_mount_cdrom_supermicro_realizes_dmtf_task_not_jid(
    redfish_mock_factory, monkeypatch
):
    """Supermicro mount_cdrom realizes a DMTF TaskService id, never a Dell JID_."""
    manager, service = redfish_mock_factory("supermicro")
    monkeypatch.setattr(
        IDracManager,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )

    result = manager.sync_invoke(
        ApiRequestType.VirtualMediaInsert,
        "mount_cdrom",
        uri_path="http://example.test/ubuntu.iso",
        device_id="USB1",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    # The task id moved with the vendor: a DMTF TaskService id, not a Dell JID_.
    assert result.data["task_id"] == service.JOB_ID
    assert not service.JOB_ID.startswith("JID_")
    assert service.last_request.path == (
        "/redfish/v1/managers/bmc_0/virtualmedia/usb1/"
        "actions/virtualmedia.insertmedia"
    )
    assert service.last_request.json() == {
        "Image": "http://example.test/ubuntu.iso",
        "Inserted": True,
        "WriteProtected": True,
    }


def test_mount_cdrom_reports_unknown_device_id(redfish_api):
    """An explicit device id with no match returns a domain error, not a raise."""
    result = redfish_api.sync_invoke(
        ApiRequestType.VirtualMediaInsert,
        "mount_cdrom",
        uri_path="http://example.test/x.iso",
        device_id="does-not-exist",
    )

    assert isinstance(result, CommandResult)
    assert result.error is not None
    assert "does-not-exist" in result.error
