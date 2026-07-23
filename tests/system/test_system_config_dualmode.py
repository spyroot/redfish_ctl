"""Dual-mode tests for system configuration commands."""

import pytest

from redfish_ctl.cmd_exceptions import InvalidArgument
from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.system.cmd_system_config import ExportSystemConfig
from redfish_ctl.system.cmd_system_import import ImportSystemConfig  # noqa: F401
from redfish_ctl.system.cmd_system_one_time_boot import ImportOneTimeBoot  # noqa: F401


def test_system_export_posts_expected_payload_in_mock_mode(
    redfish_mock, redfish_service, monkeypatch
):
    """system-export POSTs the requested export options and records the task."""
    task_state = {"TaskState": "Completed", "TaskStatus": "OK"}

    def fetch_task(self, task_id):
        assert task_id == redfish_service.JOB_ID
        return task_state

    monkeypatch.setattr(ExportSystemConfig, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(
        ApiRequestType.SystemConfigQuery,
        "sysconfig_query",
        export_format="xml",
        export_use="Clone",
        include_in_export="IncludeReadOnly",
        target="BIOS",
    )

    assert isinstance(result, CommandResult)
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == task_state

    request = redfish_service.last_request
    assert request.method == "POST"
    assert request.path.lower().endswith(
        "/redfish/v1/managers/idrac.embedded.1/actions/oem/"
        "eid_674_manager.exportsystemconfiguration"
    )
    assert request.json() == {
        "ExportFormat": "XML",
        "ShareParameters": {
            "Target": "BIOS",
            "FileName": "",
        },
        "IncludeInExport": "IncludeReadOnly",
        "ExportUse": "Clone",
    }


def test_one_time_boot_import_rejects_invalid_shutdown_type_before_post(
    redfish_mock, redfish_service
):
    """ImportOneTimeBoot rejects bad shutdown_type before any Redfish POST."""
    with pytest.raises(InvalidArgument, match="Invalid shutdown type"):
        redfish_mock.sync_invoke(
            ApiRequestType.ImportOneTimeBoot,
            "import_sysconfig",
            config="unused.xml",
            shutdown_type="power-cycle",
            host_power_state="Off",
        )

    assert all(request.method != "POST" for request in redfish_service.requests)


def test_system_import_missing_config_rejects_before_post(
    redfish_mock, redfish_service, tmp_path
):
    """ImportSystemConfig rejects a missing config path before any Redfish POST."""
    missing_config = tmp_path / "missing-scp.json"

    with pytest.raises(InvalidArgument, match="Invalid path to a config file"):
        redfish_mock.sync_invoke(
            ApiRequestType.ImportSystem,
            "import_sysconfig",
            config=str(missing_config),
        )

    assert all(request.method != "POST" for request in redfish_service.requests)


def test_system_export_posts_export_payload_and_records_task(
    redfish_mock,
    redfish_service,
    monkeypatch,
):
    """system-export POSTs the export request and reports the generated task."""
    task_state = {"TaskState": "Completed", "TaskStatus": "OK"}

    def fetch_task(self, task_id):
        assert task_id == redfish_service.JOB_ID
        return task_state

    monkeypatch.setattr(IDracManager, "fetch_task", fetch_task)

    result = redfish_mock.sync_invoke(
        ApiRequestType.SystemConfigQuery,
        "sysconfig_query",
        export_format="xml",
        export_use="Clone",
        include_in_export="IncludeReadOnly",
        target="BIOS",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["task_id"] == redfish_service.JOB_ID
    assert result.data["task_state"] == task_state
    request = redfish_service.last_request
    assert request.method == "POST"
    assert request.path.lower().endswith(
        "/redfish/v1/managers/idrac.embedded.1/actions/oem/"
        "eid_674_manager.exportsystemconfiguration"
    )
    assert request.json() == {
        "ExportFormat": "XML",
        "ShareParameters": {
            "Target": "BIOS",
            "FileName": "",
        },
        "IncludeInExport": "IncludeReadOnly",
        "ExportUse": "Clone",
    }


def test_system_import_missing_config_raises_before_post_in_mock_mode(
    redfish_mock, redfish_service, tmp_path
):
    """import_sysconfig rejects a missing config path before POSTing."""
    missing_config = tmp_path / "missing-scp.json"

    with pytest.raises(InvalidArgument, match="Invalid path to a config file"):
        redfish_mock.sync_invoke(
            ApiRequestType.ImportSystem,
            "import_sysconfig",
            config=str(missing_config),
        )

    assert all(request.method != "POST" for request in redfish_service.requests)
