"""Dual-mode tests for the ``mount_cdrom`` command (Redfish VirtualMedia.InsertMedia).

``mount_cdrom`` reuses ``ApiRequestType.VirtualMediaInsert`` (the DMTF InsertMedia
action) and auto-discovers a CD-capable, insertable VirtualMedia device. The GB300
path is exercised through the Supermicro/OpenBMC-shaped mock (``Managers/BMC_0``
with ``USB1``/``USB2``/``Slot_0``); the Dell path through the default mock tree.
"""

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

_INSERT = (ApiRequestType.VirtualMediaInsert, "mount_cdrom")


def _stub_fetch_task(monkeypatch):
    """Make ``fetch_task`` return a terminal state without polling a BMC.

    :param monkeypatch: the pytest monkeypatch fixture.
    :return: None.
    """
    monkeypatch.setattr(
        IDracManager,
        "fetch_task",
        lambda self, task_id: {"TaskState": "Completed"},
    )


def test_mount_cdrom_auto_selects_cd_device_on_gb300(
    redfish_mock_factory, monkeypatch
):
    """With no device_id, mount_cdrom picks a CD-capable device and POSTs InsertMedia.

    On the GB300-shaped tree the collection order is [USB2, USB1, Slot_0]; USB2 is
    the first member that is CD-capable *and* advertises InsertMedia, so it wins.
    """
    manager, service = redfish_mock_factory("supermicro")
    _stub_fetch_task(monkeypatch)

    result = manager.sync_invoke(
        *_INSERT, uri_path="http://example.test/gb300.iso"
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == service.JOB_ID
    assert result.data["task_state"] == {"TaskState": "Completed"}
    assert service.last_request.path == (
        "/redfish/v1/managers/bmc_0/virtualmedia/usb2/"
        "actions/virtualmedia.insertmedia"
    )
    assert service.last_request.json() == {
        "Image": "http://example.test/gb300.iso",
        "Inserted": True,
        "WriteProtected": True,
    }


def test_mount_cdrom_honors_explicit_device_id(redfish_mock_factory, monkeypatch):
    """An explicit device_id targets that member's InsertMedia action verbatim."""
    manager, service = redfish_mock_factory("supermicro")
    _stub_fetch_task(monkeypatch)

    result = manager.sync_invoke(
        *_INSERT,
        uri_path="http://example.test/gb300.iso",
        device_id="USB1",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == service.JOB_ID
    assert service.last_request.path == (
        "/redfish/v1/managers/bmc_0/virtualmedia/usb1/"
        "actions/virtualmedia.insertmedia"
    )


def test_mount_cdrom_skips_device_without_insert_action(
    redfish_mock_factory
):
    """Selecting Slot_0 (EjectMedia only) returns an error, not a POST or traceback.

    Slot_0 is CD-capable but advertises no InsertMedia action, mirroring the GB300
    corpus; the command must refuse it as a returned CommandResult.error.
    """
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        *_INSERT,
        uri_path="http://example.test/gb300.iso",
        device_id="Slot_0",
    )

    assert isinstance(result, CommandResult)
    assert result.error is not None
    assert "InsertMedia" in result.error
    assert all(request.method == "GET" for request in service.requests)


def test_mount_cdrom_reports_unknown_device_id(redfish_mock_factory):
    """An unknown device_id is a returned error listing the available devices."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(
        *_INSERT,
        uri_path="http://example.test/gb300.iso",
        device_id="NoSuchDevice",
    )

    assert isinstance(result, CommandResult)
    assert result.error is not None
    assert "NoSuchDevice" in result.error
    assert all(request.method == "GET" for request in service.requests)


def test_mount_cdrom_requires_uri_path(redfish_mock_factory):
    """A missing image URI fails fast as an error, before any BMC request."""
    manager, service = redfish_mock_factory("supermicro")

    result = manager.sync_invoke(*_INSERT, uri_path="")

    assert isinstance(result, CommandResult)
    assert result.error is not None
    assert "uri_path" in result.error
    assert service.last_request is None


def test_mount_cdrom_refuses_when_media_already_inserted(
    redfish_mock_factory
):
    """A device that already holds media returns an error unless --eject is set."""
    manager, service = redfish_mock_factory("supermicro")
    device_path = "/redfish/v1/Managers/BMC_0/VirtualMedia/USB1"
    device_state = dict(service._state(device_path))
    device_state["Inserted"] = True
    device_state["Image"] = "http://example.test/already.iso"
    service._overlay[device_path] = device_state
    service._overlay[device_path.lower()] = device_state

    result = manager.sync_invoke(
        *_INSERT,
        uri_path="http://example.test/gb300.iso",
        device_id="USB1",
    )

    assert isinstance(result, CommandResult)
    assert result.error is not None
    assert "already" in result.error
    assert all(request.method == "GET" for request in service.requests)


def test_mount_cdrom_includes_remote_credentials(
    redfish_mock, redfish_service, monkeypatch
):
    """Remote share credentials ride in the DMTF InsertMedia body (Dell mock tree)."""
    _stub_fetch_task(monkeypatch)

    result = redfish_mock.sync_invoke(
        *_INSERT,
        uri_path="http://example.test/new.iso",
        device_id="1",
        remote_username="media-user",
        remote_password="media-pass",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert redfish_service.last_request.path.endswith(
        "/virtualmedia/1/actions/virtualmedia.insertmedia"
    )
    assert redfish_service.last_request.json() == {
        "Image": "http://example.test/new.iso",
        "Inserted": True,
        "WriteProtected": True,
        "UserName": "media-user",
        "Password": "media-pass",
    }
