"""Vendor-faithful task-realization tests for the firmware-update command.

A confirmed ``#UpdateService.SimpleUpdate`` POST realizes as a task, and the
offline mock (``tests/conftest.py``) returns a VENDOR-FAITHFUL id: a Dell OEM
``JID_`` job for a Dell tree, a plain DMTF ``TaskService`` id for every other
vendor. ``firmware-update`` must surface whatever the vendor actually returned
via ``result.data['task_id']`` — never a fabricated ``JID_`` on a non-Dell box.
The chokepoint owns the sync-vs-202 decision, so the command reads the same
``task_id`` regardless of vendor; these tests prove it stays faithful to both.

Author Mus spyroot@gmail.com
"""

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

IMAGE_URI = "https://example.invalid/firmware.exe"


def test_firmware_update_realizes_dell_oem_job(redfish_mock, redfish_service):
    """firmware-update --confirm surfaces the Dell OEM JID_ job id (Dell path)."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.FirmwareUpdate,
        "firmware-update",
        image_uri=IMAGE_URI,
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert redfish_service.JOB_ID.startswith("JID_")


def test_firmware_update_realizes_dmtf_task_for_generic(redfish_mock_factory):
    """firmware-update --confirm surfaces a DMTF task id on a non-Dell box."""
    manager, service = redfish_mock_factory("generic")

    result = manager.sync_invoke(
        ApiRequestType.FirmwareUpdate,
        "firmware-update",
        image_uri=IMAGE_URI,
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == service.JOB_ID
    assert not service.JOB_ID.startswith("JID_")
