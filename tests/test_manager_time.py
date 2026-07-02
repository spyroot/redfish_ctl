"""Offline tests for manager-time — read/set the BMC (Manager) clock.

Covers the pure PATCH-payload builder (read vs --now vs --set vs --offset) and
the read-by-default path against the mock managers. The live write (PATCH
DateTime) is exercised against real hardware, not here; these stay offline.
"""
import re

from idrac_ctl.idrac_shared import ApiRequestType
from idrac_ctl.manager.cmd_manager_time import build_time_payload


def test_payload_none_when_no_write_requested():
    """With neither --now nor --set, the builder returns None (read-only)."""
    assert build_time_payload(False, None, None) is None


def test_payload_explicit_datetime():
    """--set passes an explicit ISO-8601 DateTime straight through."""
    p = build_time_payload(False, "2026-07-02T20:00:00+00:00", None)
    assert p == {"DateTime": "2026-07-02T20:00:00+00:00"}


def test_payload_explicit_wins_over_now_and_carries_offset():
    """--set wins over --now, and --offset is added as DateTimeLocalOffset."""
    p = build_time_payload(True, "2026-01-01T00:00:00+00:00", "+00:00")
    assert p == {"DateTime": "2026-01-01T00:00:00+00:00",
                 "DateTimeLocalOffset": "+00:00"}


def test_payload_now_is_utc_offset_form():
    """--now yields a current UTC time in Redfish's +00:00 offset form (not 'Z')."""
    p = build_time_payload(True, None, None)
    assert set(p) == {"DateTime"}
    # ISO-8601 ending in an explicit +00:00 offset, never a trailing 'Z'.
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$", p["DateTime"])


def test_manager_time_read_lists_managers(redfish_mock_factory):
    """Read-by-default returns a row per Manager with its DateTime field."""
    mgr, _ = redfish_mock_factory("supermicro")
    res = mgr.sync_invoke(ApiRequestType.ManagerTime, "manager-time")
    assert isinstance(res.data, list) and res.data, "no manager rows"
    for row in res.data:
        assert row.get("Manager")           # a manager id was resolved
        assert "DateTime" in row             # the field is reported (may be None)
        assert "WriteStatus" not in row      # read path must not write
