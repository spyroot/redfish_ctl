"""Offline tests for the action-lister and the two example action commands.

All run against the GB300 corpus via redfish_mock_factory('supermicro'); the mock
records POSTs so we can assert what fires. These prove the CLI commands wrap
invoke_action correctly and inherit its safety guard.
"""
from redfish_ctl.idrac_shared import ApiRequestType


def _post_count(svc):
    return sum(1 for r in svc.requests if r.method == "POST")


def test_action_list_inventories_actions_with_levels(redfish_mock_factory):
    """`actions` lists discovered targets tagged with their risk level, no POST."""
    mgr, svc = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.ActionList, "action_list")
    assert isinstance(result.data, list) and result.data
    fulls = {r["FullType"] for r in result.data}
    # headline actions across systems / managers / event service show up
    assert "#ComputerSystem.Reset" in fulls
    assert "#Manager.Reset" in fulls
    assert "#EventService.SubmitTestEvent" in fulls
    levels = {r["Level"] for r in result.data}
    assert "destructive" in levels and "reversible" in levels
    # every row carries a concrete target, and listing never mutates
    assert all(r["Target"] for r in result.data)
    assert _post_count(svc) == 0


def test_event_submit_test_fires(redfish_mock_factory):
    """event-submit-test is reversible, so it POSTs the test event by default."""
    mgr, svc = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.EventSubmitTest, "event_submit_test",
                             message_id="Alert.1.0.TestEvent")
    assert result.data.get("executed") is True
    assert _post_count(svc) == 1
    assert svc.last_request.json()["MessageId"] == "Alert.1.0.TestEvent"


def test_event_submit_test_dry_run(redfish_mock_factory):
    """--dry_run on a reversible action still suppresses the POST."""
    mgr, svc = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.EventSubmitTest, "event_submit_test",
                             message_id="Alert.1.0.TestEvent", dry_run=True)
    assert result.data["dry_run"] is True
    assert _post_count(svc) == 0


def test_system_reset_dry_run_by_default(redfish_mock_factory):
    """system-reset previews (no POST) unless --confirm, and targets the host."""
    mgr, svc = redfish_mock_factory("supermicro")
    preview = mgr.sync_invoke(ApiRequestType.SystemReset, "system_reset",
                              reset_type="GracefulRestart")
    assert preview.data["dry_run"] is True
    # resolves the host system (System_0), not the GPU baseboard
    assert preview.data["target"] == "/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset"
    assert _post_count(svc) == 0


def test_system_reset_with_confirm_fires(redfish_mock_factory):
    """system-reset --confirm POSTs the ResetType payload to the host target."""
    mgr, svc = redfish_mock_factory("supermicro")
    result = mgr.sync_invoke(ApiRequestType.SystemReset, "system_reset",
                             reset_type="ForceRestart", confirm=True)
    assert result.data.get("executed") is True
    assert _post_count(svc) == 1
    assert svc.last_request.json() == {"ResetType": "ForceRestart"}
