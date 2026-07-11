"""Offline tests for CSDL-backed Redfish action validation."""

import copy


def _write_reset_csdl(schema_dir):
    """Create a minimal ComputerSystem.Reset CSDL cache for tests."""
    (schema_dir / "ComputerSystem_v1.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
  <edmx:DataServices>
    <Schema Namespace="ComputerSystem.v1_0_0" xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <Action Name="Reset" IsBound="true">
        <Parameter Name="ComputerSystem" Type="ComputerSystem.v1_0_0.ComputerSystem" Nullable="false"/>
        <Parameter Name="ResetType" Type="Resource.ResetType"/>
      </Action>
    </Schema>
    <Schema Namespace="Resource" xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <EnumType Name="ResetType">
        <Member Name="GracefulRestart"/>
        <Member Name="ForceRestart"/>
      </EnumType>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
""",
        encoding="utf-8",
    )


def _system_without_inline_allowable_values(redfish_service):
    """Return the Dell ComputerSystem fixture with inline reset choices removed."""
    system = copy.deepcopy(
        redfish_service._state("/redfish/v1/Systems/System.Embedded.1")
    )
    reset = system["Actions"]["#ComputerSystem.Reset"]
    reset.pop("ResetType@Redfish.AllowableValues", None)
    return system


def test_invoke_action_rejects_invalid_csdl_enum_without_post(
    redfish_mock,
    redfish_service,
    tmp_path,
    monkeypatch,
):
    """A CSDL enum fallback blocks invalid action payload values before POST."""
    _write_reset_csdl(tmp_path)
    monkeypatch.setenv("REDFISH_CSDL_DIR", str(tmp_path))
    system = _system_without_inline_allowable_values(redfish_service)
    redfish_service._overlay["/redfish/v1/Systems/System.Embedded.1"] = system
    redfish_service._overlay["/redfish/v1/systems/system.embedded.1"] = system

    result = redfish_mock.invoke_action(
        "/redfish/v1/Systems/System.Embedded.1",
        "Reset",
        payload={"ResetType": "BadReset"},
        full_action_type="#ComputerSystem.Reset",
        confirm=True,
    )

    assert result.error == (
        "invalid value for ComputerSystem.Reset ResetType: BadReset; "
        "allowed: ForceRestart, GracefulRestart"
    )
    assert result.data["payload"] == {"ResetType": "BadReset"}
    assert result.data["validation_errors"] == [
        {
            "parameter": "ResetType",
            "value": "BadReset",
            "allowed": ["ForceRestart", "GracefulRestart"],
        }
    ]
    assert [
        request for request in redfish_service.requests
        if request.method == "POST"
    ] == []
