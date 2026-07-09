"""Dual-mode test for the BIOS inventory query command.

Runs offline by default against the mock service (using the iDRAC-shaped fixture
tests/idrac_fixtures/_redfish_v1_Systems_System.Embedded.1_Bios.json), and against
real hardware when IDRAC_IP is set. Mirrors tests/test_cmd_boot_dualmode.py: invoke
the command through sync_invoke and assert the CommandResult shape.

This is also the regression guard for the IDRAC_API.BIOS member: BiosQuery.execute
builds the URL as f"{self.idrac_manage_servers}{IDRAC_API.BIOS}", so a missing BIOS
member raises AttributeError before any request is made.

Author Mus spyroot@gmail.com
"""
import json

from idrac_ctl.idrac_shared import IDRAC_API, ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def test_idrac_api_has_bios_member():
    """IDRAC_API exposes a BIOS base path so BiosQuery can build its URL."""
    # The base resource hangs off idrac_manage_servers (.../System.Embedded.1).
    assert IDRAC_API.BIOS == "/Bios"


def test_bios_inventory_query(redfish_api):
    """bios_inventory returns a JSON-serializable CommandResult from /Bios."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BiosQuery, "bios_inventory"
    )
    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    # the payload must be JSON-serializable (CLI renders it as JSON)
    json.dumps(result.data)
    # in mock mode the fixture carries the iDRAC BIOS resource + attributes
    assert result.data["@odata.id"].endswith("/Bios")
    assert result.data["@odata.type"].startswith("#Bios.")
    attributes = result.data["Attributes"]
    assert isinstance(attributes, dict)
    assert attributes["BootMode"] in {"Uefi", "Bios"}


def test_bios_inventory_attr_only_returns_only_attributes(redfish_api):
    """attr_only trims the payload down to just the Attributes map."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BiosQuery, "bios_inventory", attr_only=True
    )
    assert isinstance(result, CommandResult)
    assert set(result.data.keys()) == {"Attributes"}
    assert isinstance(result.data["Attributes"], dict)


def test_bios_inventory_filter_selects_matching_attribute(redfish_api):
    """A --filter value narrows the result to the matching BIOS attribute(s)."""
    result = redfish_api.sync_invoke(
        ApiRequestType.BiosQuery, "bios_inventory", attr_filter="ProcCStates"
    )
    assert isinstance(result, CommandResult)
    assert "ProcCStates" in result.data
    # filtering returns a flat {attr: value} map, not the full Bios resource
    assert "@odata.id" not in result.data


def test_bios_inventory_filter_file_selects_list_attributes(
    redfish_mock, redfish_service, tmp_path
):
    """A JSON list passed via --from_file narrows BIOS output to those keys."""
    query_file = tmp_path / "bios_query.json"
    query_file.write_text(json.dumps(["ProcCStates", "SysMemSize"]))

    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosQuery,
        "bios_inventory",
        attr_filter_file=str(query_file),
    )

    assert isinstance(result, CommandResult)
    assert result.data == {
        "ProcCStates": "Disabled",
        "SysMemSize": "768 GB",
    }
    json.dumps(result.data)
    assert {request.method for request in redfish_service.requests} == {"GET"}


def test_bios_inventory_filter_list_saves_filtered_attributes(
    redfish_mock, redfish_service, tmp_path
):
    """A comma-separated BIOS filter returns and saves only matching attributes."""
    output_file = tmp_path / "bios_filter_save.json"

    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosQuery,
        "bios_inventory",
        attr_filter="ProcCStates,SysMemSize",
        filename=str(output_file),
    )

    assert isinstance(result, CommandResult)
    assert result.data == {
        "ProcCStates": "Disabled",
        "SysMemSize": "768 GB",
    }
    saved = json.loads(output_file.read_text())
    assert saved == result.data
    assert set(saved) == {"ProcCStates", "SysMemSize"}
    json.dumps(result.data)
    assert {request.method for request in redfish_service.requests} == {"GET"}
