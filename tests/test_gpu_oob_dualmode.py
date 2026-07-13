"""Dual-mode smoke tests for optional out-of-band Redfish read commands."""

import pytest

from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

GPU_OOB_READ_COMMANDS = (
    (ApiRequestType.MetricReportDefinitions, "metric-definitions"),
    (ApiRequestType.MetricReports, "metric-reports"),
    (ApiRequestType.ComponentIntegrity, "component-integrity"),
    (ApiRequestType.NetworkAdapters, "network-adapters"),
    (ApiRequestType.NvLinkPorts, "nvlink-ports"),
)


@pytest.mark.parametrize(("request_type", "command_name"), GPU_OOB_READ_COMMANDS)
def test_gpu_oob_commands_return_list_payloads(redfish_api, request_type, command_name):
    """OOB read commands return lists and tolerate missing optional collections."""
    result = redfish_api.sync_invoke(request_type, command_name)

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    assert result.error is None

    if redfish_api.idrac_ip == "mock-idrac":
        if request_type is ApiRequestType.NetworkAdapters:
            adapter_ids = {row["Id"] for row in result.data}
            adapter_classes = {row["DeviceClass"] for row in result.data}

            assert adapter_ids == {"NIC.Integrated.1-1", "DPU.Slot.2-1"}
            assert adapter_classes == {"NIC", "DPU"}
        else:
            assert result.data == []
