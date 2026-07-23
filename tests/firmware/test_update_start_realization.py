"""Vendor-faithful task-realization tests for the update-start command.

A confirmed ``#UpdateService.StartUpdate`` POST realizes as a task. The offline
mock (``tests/conftest.py``) returns a VENDOR-FAITHFUL id: a Dell OEM ``JID_``
job for a Dell tree, a plain DMTF ``TaskService`` id for every other vendor.
``update-start`` routes through ``invoke_action``, so ``result.data['task_id']``
must be whatever the vendor returned — a ``JID_`` only on Dell, never fabricated
for a non-Dell box. These tests assert realization against BOTH vendor shapes.

Author Mus spyroot@gmail.com
"""

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

UPDATE_SERVICE_PATH = "/redfish/v1/UpdateService"
START_UPDATE_TARGET = (
    "/redfish/v1/UpdateService/Actions/UpdateService.StartUpdate"
)


def _start_update_service():
    """Return an UpdateService body exposing the StartUpdate action.

    :return: an UpdateService resource dict whose Actions block advertises
        ``#UpdateService.StartUpdate`` at ``START_UPDATE_TARGET``.
    """
    return {
        "@odata.id": UPDATE_SERVICE_PATH,
        "@odata.type": "#UpdateService.v1_14_0.UpdateService",
        "Id": "UpdateService",
        "Name": "Update Service",
        "Actions": {
            "#UpdateService.StartUpdate": {
                "target": START_UPDATE_TARGET,
            }
        },
    }


def _overlay_start_update(service):
    """Overlay a StartUpdate-capable UpdateService under both path casings.

    :param service: the ``MockRedfishService`` whose overlay to seed so the
        UpdateService resource advertises StartUpdate for the command to resolve.
    """
    service._overlay[UPDATE_SERVICE_PATH] = _start_update_service()
    service._overlay[UPDATE_SERVICE_PATH.lower()] = _start_update_service()


def test_update_start_realizes_dell_oem_job(redfish_mock, redfish_service):
    """update-start --confirm surfaces the Dell OEM JID_ job id (Dell path)."""
    _overlay_start_update(redfish_service)

    result = redfish_mock.sync_invoke(
        ApiRequestType.UpdateStart,
        "update-start",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert redfish_service.JOB_ID.startswith("JID_")


def test_update_start_realizes_dmtf_task_for_generic(redfish_mock_factory):
    """update-start --confirm surfaces a DMTF task id on a non-Dell box."""
    manager, service = redfish_mock_factory("generic")
    _overlay_start_update(service)

    result = manager.sync_invoke(
        ApiRequestType.UpdateStart,
        "update-start",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == service.JOB_ID
    assert not service.JOB_ID.startswith("JID_")
