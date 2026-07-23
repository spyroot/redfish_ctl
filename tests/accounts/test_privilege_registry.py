"""Dual-mode test for the privilege-registry query command.

Covers ``query_privilege_registry`` (CLI verb ``privilege-registry``), a pure
DMTF read of the ``#PrivilegeRegistry`` resource. The command resolves the
manager member and reads ``<manager>/PrivilegeRegistry``; the offline fixture
tree serves the standard Redfish PrivilegeRegistry document, so the test runs
with no BMC and no network. Runs live when ``IDRAC_IP`` is set, otherwise mock.

Author Mus spyroot@gmail.com
"""
import json

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult


def test_privilege_registry_returns_registry_document(redfish_api):
    """query_privilege_registry returns the #PrivilegeRegistry resource."""
    result = redfish_api.sync_invoke(
        ApiRequestType.PrivilegeRegistry,
        "query_privilege_registry",
    )

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, dict)
    # Payload must be JSON-serializable (it is rendered by the output adapter).
    json.dumps(result.data)
    assert result.data.get("Id") == "PrivilegeRegistry"
    assert str(result.data.get("@odata.type", "")).startswith(
        "#PrivilegeRegistry."
    )
    assert result.error is None


def test_privilege_registry_targets_the_registry_path(redfish_api):
    """The read targets a ``/PrivilegeRegistry`` resource URI."""
    result = redfish_api.sync_invoke(
        ApiRequestType.PrivilegeRegistry,
        "query_privilege_registry",
    )

    assert isinstance(result, CommandResult)
    uri = str(result.data.get("@odata.id", "")) if isinstance(result.data, dict) else ""
    assert uri.rstrip("/").endswith("/PrivilegeRegistry")
