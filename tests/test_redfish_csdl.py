"""Offline tests for Redfish CSDL action metadata helpers."""

from redfish_ctl.redfish_csdl import action_parameters_for


def test_csdl_action_parameters_resolve_enum_values(tmp_path):
    """CSDL action parsing exposes enum choices when inline action info is absent."""
    (tmp_path / "ComputerSystem_v1.xml").write_text(
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

    params = action_parameters_for("#ComputerSystem.Reset", tmp_path)

    assert tuple(params) == ("ResetType",)
    assert params["ResetType"].type_name == "Resource.ResetType"
    assert params["ResetType"].allowable_values == (
        "GracefulRestart",
        "ForceRestart",
    )
