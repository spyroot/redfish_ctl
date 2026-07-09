"""Dual-mode tests for account service read commands."""
import json

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_account_service_query_returns_service_document(redfish_api):
    """query_account_svc returns the AccountService resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.QueryAccountService,
        "query_account_svc",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == "/redfish/v1/AccountService"
    assert result.data["Accounts"]["@odata.id"] == (
        "/redfish/v1/AccountService/Accounts"
    )
    assert result.data["SupportedAccountTypes"] == ["Redfish", "SNMP", "OEM"]


def test_account_service_query_filters_account_types(redfish_api):
    """query_account_svc can project supported account types."""
    result = redfish_api.sync_invoke(
        ApiRequestType.QueryAccountService,
        "query_account_svc",
        account_types=True,
    )

    assert isinstance(result, CommandResult)
    assert result.data == ["Redfish", "SNMP", "OEM"]


def test_account_service_query_filters_roles_link(redfish_api):
    """query_account_svc maps the roles filter to the Roles property."""
    result = redfish_api.sync_invoke(
        ApiRequestType.QueryAccountService,
        "query_account_svc",
        schema_filter="roles",
    )

    assert isinstance(result, CommandResult)
    assert result.data == {"@odata.id": "/redfish/v1/AccountService/Roles"}


def test_privilege_registry_query_returns_manager_privileges(redfish_api):
    """query_privilege_registry reads the manager PrivilegeRegistry resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.PrivilegeRegistry,
        "query_privilege_registry",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    json.dumps(result.data)
    assert result.data["@odata.id"] == (
        "/redfish/v1/Managers/iDRAC.Embedded.1/PrivilegeRegistry"
    )
    # PrivilegesUsed is a flat array of Redfish privilege names (DSP8011),
    # and each Mapping's OperationMap.<method> is a list of OperationPrivilege
    # objects ({"Privilege": [...]}), not bare strings.
    assert "Login" in result.data["PrivilegesUsed"]
    manager_map = result.data["Mappings"][0]["OperationMap"]
    assert manager_map["GET"][0]["Privilege"] == ["Login"]
