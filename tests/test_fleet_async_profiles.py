"""Fleet async-workflow proof across mixed vendor lenses (C3).

The deliberate fleet shape: fire an async mutation at N BMCs, collect the N
task ids, walk away, and fetch each task later. With vendor bound per
CONNECTION, one Dell-based command family serves a mixed fleet: the dell
connection yields a ``JID_`` OEM job id and its fetch consults the
``/Oem/Dell/Jobs`` queue, while every other connection yields a DMTF
TaskService id and polls ``/redfish/v1/TaskService/Tasks/{id}`` — never a
Dell OEM URL. Transport is stubbed at the manager seams (no network); the
profile binding itself is real classification over each lens's committed
ServiceRoot fixture.
Root-module test: flat placement mirrors redfish_ctl/redfish_manager.py.

Author Mus spyroot@gmail.com
"""
import json
from pathlib import Path

from redfish_ctl.idrac_manager import IDracManager
from redfish_ctl.idrac_shared import RedfishApiRespond

_TESTS_DIR = Path(__file__).resolve().parent

# lens -> (ServiceRoot fixture, vendor-faithful task id). Only the dell lens
# ever carries a JID_; the DMTF lenses use a plain TaskService monitor id.
_FLEET = {
    "dell": (_TESTS_DIR / "idrac_fixtures" / "_redfish_v1.json",
             "JID_000000000001"),
    "supermicro": (_TESTS_DIR / "supermicro_fixtures" / "_redfish_v1.json", "1"),
    "hpe": (_TESTS_DIR / "hpe_fixtures" / "_redfish_v1.json", "2"),
    "generic": (_TESTS_DIR / "generic_fixtures" / "_redfish_v1.json", "3"),
}


class _AcceptedResponse:
    """A 202 Accepted write response carrying the task monitor Location."""

    def __init__(self, task_id):
        self.status_code = 202
        self.headers = {
            "Location": f"/redfish/v1/TaskService/Tasks/{task_id}"}

    def json(self):
        """Return an empty body, as a 202 task-accept typically has none.

        :return: an empty dict.
        """
        return {}


class _TaskResponse:
    """A 200 task read; body shape depends on the URL the fake GET served."""

    def __init__(self, body):
        self.status_code = 200
        self.headers = {}
        self._body = body

    def json(self):
        """Return the configured task body.

        :return: the body dict this response was built with.
        """
        return self._body


def _bind_fleet():
    """Build one manager per lens with its profile bound by real classification.

    :return: dict of lens -> manager, each with the lens root attached.
    """
    fleet = {}
    for lens, (root_path, _tid) in _FLEET.items():
        mgr = IDracManager(host=f"fleet-{lens}", username="root", password="x")
        mgr.__dict__["_service_root"] = json.loads(root_path.read_text())
        # Touch the property once: classification caches per connection.
        assert mgr.vendor_profile is not None
        fleet[lens] = mgr
    return fleet


def test_fleet_async_collect_then_fetch(monkeypatch):
    """Fire async at every lens, collect ids now, fetch each task later.

    Phase 1 (fire): each async POST returns 202 + Location; the collected id
    is the lens-faithful one (dell JID_, others DMTF).
    Phase 2 (fetch): each ``fetch_task`` polls that lens's OWN task resource —
    the dell connection consults ``/Oem/Dell/Jobs``, no other connection ever
    touches a Dell OEM URL.
    """
    fleet = _bind_fleet()
    calls = {lens: [] for lens in fleet}

    # --- phase 1: async fan-out, collect ids -----------------------------
    task_ids = {}
    for lens, mgr in fleet.items():
        expected_id = _FLEET[lens][1]

        async def _fake_post(*args, _tid=expected_id, **kwargs):
            """Serve the lens's 202-accept without any network."""
            return _AcceptedResponse(_tid), RedfishApiRespond.AcceptedTaskGenerated

        monkeypatch.setattr(mgr, "api_async_post_until_complete", _fake_post)
        result, api_resp = mgr.base_post(
            "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            payload={"ResetType": "On"}, do_async=True)
        assert api_resp == RedfishApiRespond.AcceptedTaskGenerated
        task_ids[lens] = result.data["task_id"]

    assert task_ids["dell"].startswith("JID_")
    for lens in ("supermicro", "hpe", "generic"):
        assert not task_ids[lens].startswith("JID_")

    # --- phase 2: fetch each collected id later --------------------------
    for lens, mgr in fleet.items():
        # The Dell job read builds its URL from the manager's members path;
        # seed the cached property so no discovery crawl is needed.
        mgr.__dict__["idrac_members"] = "/redfish/v1/Managers/iDRAC.Embedded.1"

        def _fake_get(url, hdr=None, _lens=lens, **kwargs):
            """Record the polled URL and serve a terminal task/job body."""
            calls[_lens].append(url)
            if "/Oem/Dell/Jobs/" in url:
                return _TaskResponse({"JobState": "Completed"})
            return _TaskResponse({"TaskState": "Completed", "TaskStatus": "OK"})

        monkeypatch.setattr(fleet[lens], "api_get_call", _fake_get)
        state = fleet[lens].fetch_task(task_ids[lens], sleep_time=0)
        assert state is not None
        assert state.name == "Completed"

    # The dell connection fetched through the OEM job queue.
    assert any("/Oem/Dell/Jobs/" in url for url in calls["dell"])
    # No other connection ever touched a Dell OEM URL; each polled the DMTF
    # TaskService monitor for its own collected id.
    for lens in ("supermicro", "hpe", "generic"):
        assert calls[lens], f"{lens} never polled"
        assert all("/Oem/Dell/" not in url for url in calls[lens])
        assert any(
            f"/TaskService/Tasks/{task_ids[lens]}" in url
            for url in calls[lens])


def test_fleet_profiles_bind_per_connection():
    """Each fleet member resolves ITS box's profile through one manager class.

    Ancestry says Dell everywhere (every command bases IDracManager); the
    resolved chokepoint set must follow the connection instead.
    """
    fleet = _bind_fleet()
    assert type(fleet["dell"].vendor_profile).vendor == "dell"
    assert type(fleet["supermicro"].vendor_profile).vendor == "supermicro"
    assert type(fleet["hpe"].vendor_profile).vendor == "hpe"
    assert type(fleet["generic"].vendor_profile).vendor == "generic"
