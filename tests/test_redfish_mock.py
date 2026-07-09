"""Offline tests that drive the real Redfish HTTP layer against the captured
DMTF mockup tree (redfish_ctl/json_responses) via the ``redfish_mock`` fixture.

This is the foundation for converting the live (@pytest.mark.live) command tests
to run without hardware: the same code path that talks to a real iDRAC runs here
against static mockup JSON. See IMPROVEMENT_PLAN.md item 5.

Author Mus spyroot@gmail.com
"""
import json


def test_get_managers_collection(redfish_mock):
    """A GET to a known Redfish path returns the captured mockup payload."""
    resp = redfish_mock.api_get_call(
        "https://mock-idrac/redfish/v1/Managers", {}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["@odata.id"] == "/redfish/v1/Managers"
    assert isinstance(data.get("Members"), list)


def test_get_chassis_resource(redfish_mock):
    """A nested resource path also resolves to its mockup file."""
    resp = redfish_mock.api_get_call(
        "https://mock-idrac/redfish/v1/Chassis/1U", {}
    )
    assert resp.status_code == 200
    assert resp.json()["@odata.id"] == "/redfish/v1/Chassis/1U"


def test_unknown_path_returns_404(redfish_mock):
    """A path with no captured fixture fails loudly with 404, not a false 200."""
    resp = redfish_mock.api_get_call(
        "https://mock-idrac/redfish/v1/Nonexistent", {}
    )
    assert resp.status_code == 404


# --- mutating verbs: assert the request the client SENDS, offline ------------

def test_post_action_returns_job_location(redfish_mock, redfish_service):
    """A POST to an Action returns 202 with a JID task Location, like iDRAC."""
    url = ("https://mock-idrac/redfish/v1/Systems/System.Embedded.1/"
           "Actions/ComputerSystem.Reset")
    resp = redfish_mock.api_post_call(
        url, json.dumps({"ResetType": "On"}), {}
    )
    assert resp.status_code == 202
    assert "JID_" in resp.headers.get("Location", "")
    # the client sent exactly the payload we asked for
    assert redfish_service.last_request.json() == {"ResetType": "On"}


def test_patch_merges_and_is_visible_on_get(redfish_mock, redfish_service):
    """PATCH records the payload and a later GET reflects the merged state."""
    url = ("https://mock-idrac/redfish/v1/Systems/System.Embedded.1/"
           "Bios/Settings")
    body = {"Attributes": {"BootMode": "Uefi"}}
    resp = redfish_mock.api_patch_call(url, json.dumps(body), {})
    assert resp.status_code == 200
    assert redfish_service.last_request.json() == body
    # follow-up GET sees the overlay applied by the PATCH
    after = redfish_mock.api_get_call(url, {}).json()
    assert after["Attributes"]["BootMode"] == "Uefi"


def test_delete_returns_ok(redfish_mock):
    """DELETE on a resource returns a success status, no hardware involved."""
    url = ("https://mock-idrac/redfish/v1/Systems/System.Embedded.1/"
           "Storage/Volumes/Disk.Virtual.0")
    resp = redfish_mock.api_delete_call(url, {})
    assert resp.status_code == 200


# --- dual-mode fixture: offline by default, live when IDRAC_IP is set --------

def test_redfish_api_runs_in_mock_mode(redfish_api):
    """Without IDRAC_IP, redfish_api yields a mock-backed client that works offline."""
    resp = redfish_api.api_get_call(
        "https://mock-idrac/redfish/v1/Managers", {}
    )
    assert resp.status_code == 200
    assert resp.json()["@odata.id"] == "/redfish/v1/Managers"
