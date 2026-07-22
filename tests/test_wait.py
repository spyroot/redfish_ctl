"""Offline tests for the wait command (poll BMC ServiceRoot until reachable).

Any HTTP response means the BMC is up; a connection error means not yet. Tests
use tiny timeout/interval and mock requests.get so nothing sleeps meaningfully
and no real host is touched.
"""
import requests

from redfish_ctl.cmd_wait import probe_reachable, wait_for
from redfish_ctl.idrac_shared import ApiRequestType


def test_wait_for_satisfied_predicate():
    """wait_for returns satisfied once the generic predicate becomes True, echoing the label."""
    calls = {"n": 0}

    def ready():
        calls["n"] += 1
        return calls["n"] >= 3        # True on the third poll
    res = wait_for(ready, description="media mounted", timeout=5, interval=0)
    assert res["satisfied"] is True
    assert res["waiting_for"] == "media mounted"


def test_wait_for_times_out():
    """A predicate that never holds times out with satisfied=False."""
    res = wait_for(lambda: False, description="never", timeout=0.05, interval=0)
    assert res["satisfied"] is False
    assert res["waiting_for"] == "never"


def test_wait_for_predicate_exception_is_not_yet():
    """A raising predicate counts as 'not yet', not a crash."""
    def boom():
        raise RuntimeError("still working")
    res = wait_for(boom, description="job done", timeout=0.05, interval=0)
    assert res["satisfied"] is False


def test_wait_for_invert_first_records_precondition():
    """invert_first waits for False (e.g. down) then True (up); records precondition_met."""
    seq = iter([True, False, True, True])   # up, down, up, ...

    def state():
        return next(seq, True)
    res = wait_for(state, description="cycle", timeout=5, interval=0, invert_first=True)
    assert res["precondition_met"] is True   # observed the False phase
    assert res["satisfied"] is True


class _Resp:
    status_code = 200


def _patch_get(fake):
    orig, requests.get = requests.get, fake
    return orig


def test_probe_reachable_true_on_any_response():
    """Any HTTP response (even a raised-for-status-worthy 403) counts as reachable."""
    orig = _patch_get(lambda *a, **k: _Resp())
    try:
        assert probe_reachable("https://x/redfish/v1/", None, False, 1) is True
    finally:
        requests.get = orig


def test_probe_reachable_false_on_connection_error():
    """A connection error / timeout means the BMC is not up yet."""
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("down")
    orig = _patch_get(boom)
    try:
        assert probe_reachable("https://x/redfish/v1/", None, False, 1) is False
    finally:
        requests.get = orig


def test_wait_returns_reachable(redfish_mock_factory):
    """wait returns reachable=True with no error once the ServiceRoot answers."""
    mgr, _ = redfish_mock_factory("hpe")
    orig = _patch_get(lambda *a, **k: _Resp())
    try:
        res = mgr.sync_invoke(ApiRequestType.WaitReady, "wait",
                              wait_timeout=5, wait_interval=0)
    finally:
        requests.get = orig
    assert res.data["reachable"] is True
    assert res.error is None
    assert res.data["target"]


def test_wait_times_out_when_unreachable(redfish_mock_factory):
    """wait reports reachable=False + an error when the BMC never answers."""
    mgr, _ = redfish_mock_factory("hpe")
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("down")
    orig = _patch_get(boom)
    try:
        res = mgr.sync_invoke(ApiRequestType.WaitReady, "wait",
                              wait_timeout=0.05, wait_interval=0)
    finally:
        requests.get = orig
    assert res.data["reachable"] is False
    assert res.error and "not reachable" in res.error


def test_wait_reboot_cycle_reports_went_down(redfish_mock_factory):
    """--reboot-cycle records the down phase before it comes back up."""
    mgr, _ = redfish_mock_factory("hpe")
    calls = {"n": 0}

    def flaky(*a, **k):
        # first probe: down; subsequent: up
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError("down")
        return _Resp()
    orig = _patch_get(flaky)
    try:
        res = mgr.sync_invoke(ApiRequestType.WaitReady, "wait",
                              wait_timeout=5, wait_interval=0, wait_reboot_cycle=True)
    finally:
        requests.get = orig
    assert res.data["went_down"] is True
    assert res.data["reachable"] is True


def test_manager_reboot_wait_attaches_wait_block(redfish_mock_factory, monkeypatch):
    """manager-reboot --wait attaches the reachability wait result to its output.

    The reset POST + task path is stubbed so the test isolates the new --wait
    behavior: after the reset, the reboot-cycle wait result is attached.
    """
    import redfish_ctl.cmd_wait as cw
    from redfish_ctl.idrac_manager import IDracManager
    from redfish_ctl.idrac_shared import RedfishApiRespond
    from redfish_ctl.redfish_manager import CommandResult

    mgr, _ = redfish_mock_factory("hpe")
    monkeypatch.setattr(IDracManager, "idrac_members", "/redfish/v1/Managers/1", raising=False)
    monkeypatch.setattr(
        IDracManager, "base_post",
        lambda self, *a, **k: (CommandResult({"Status": "ok"}, None, None, None), RedfishApiRespond.Ok))
    monkeypatch.setattr(
        cw, "wait_reachable",
        lambda *a, **k: {"reachable": True, "went_down": True, "waited_s": 0.1})

    res = mgr.sync_invoke(ApiRequestType.ManagerReset, "manager_reset", do_wait=True)
    assert res.data["Status"] == "ok"
    assert res.data["wait"] == {"reachable": True, "went_down": True, "waited_s": 0.1}
