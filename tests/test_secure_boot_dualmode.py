"""Dual-mode test for the read-only secure-boot command."""
import json

from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.redfish_manager import CommandResult


def test_secure_boot_returns_fixture_database_rows_without_mutation(
    monkeypatch,
    redfish_mock_factory,
):
    """secure-boot returns fixture database rows with GET-only traffic."""
    monkeypatch.delenv("IDRAC_IP", raising=False)
    redfish_api, redfish_service = redfish_mock_factory("hpe")

    result = redfish_api.sync_invoke(ApiRequestType.SecureBoot, "secure-boot")

    assert isinstance(result, CommandResult)
    assert isinstance(result.data, list)
    assert result.data
    json.dumps(result.data, sort_keys=True)

    assert {
        row["DatabaseId"]
        for row in result.data
        if row["DatabaseId"] is not None
    } == {"PKDefault", "KEKDefault", "dbDefault", "dbxDefault"}
    assert {
        "System": "1",
        "SecureBootEnable": False,
        "SecureBootMode": "UserMode",
        "SecureBootCurrentBoot": "Disabled",
        "Database": "PKDefault",
        "DatabaseId": "PKDefault",
        "Certificates": 1,
    } in result.data
    assert redfish_service.requests
    assert all(
        request.method not in {"POST", "PATCH", "DELETE"}
        for request in redfish_service.requests
    )
