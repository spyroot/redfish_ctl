"""Offline tests for the vendor capability report."""

import json
import os
import subprocess
import sys
from pathlib import Path

from redfish_ctl.redfish_manager_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult
from redfish_ctl.vendors import capability_report, get_vendor

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_vendor_capabilities_to_dict_is_json_ready():
    """Vendor capability profiles serialize tuples as JSON arrays."""
    payload = get_vendor("dell").to_dict()

    json.dumps(payload, sort_keys=True)
    assert payload["vendor"] == "dell"
    assert payload["oem_prefix"] == "Dell"
    assert isinstance(payload["schedulable_uris"], list)
    assert any("ComputerSystem.Reset" in uri for uri in payload["schedulable_uris"])


def test_capability_report_returns_machine_readable_vendor_profiles(redfish_mock):
    """capability-report exposes every registered profile without BMC reads."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.CapabilityReport,
        "capability-report",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.discovered is None
    assert result.extra is None
    json.dumps(result.data, sort_keys=True)

    assert result.data["schema"] == "redfish_ctl.capability_report.v1"
    assert result.data["summary"]["vendor_count"] >= 4
    assert set(result.data["vendors"]) >= {"generic", "dell", "hpe", "supermicro"}
    assert result.data["vendors"]["dell"]["query_select"] is True
    assert isinstance(result.data["vendors"]["dell"]["schedulable_uris"], list)
    assert result.data["vendors"]["supermicro"]["query_select"] is False


def test_capability_report_helper_is_exported_for_library_consumers():
    """Library users can import the same report helper as the CLI command."""
    payload = capability_report("supermicro")

    assert payload["summary"] == {"vendor_count": 1}
    assert set(payload["vendors"]) == {"supermicro"}


def test_capability_report_filters_one_vendor(redfish_mock):
    """capability-report --vendor emits one named profile plus summary metadata."""
    result = redfish_mock.sync_invoke(
        ApiRequestType.CapabilityReport,
        "capability-report",
        vendor="hpe",
    )

    assert result.error is None
    assert result.data["summary"] == {"vendor_count": 1}
    assert set(result.data["vendors"]) == {"hpe"}
    assert result.data["vendors"]["hpe"]["query_expand"] is True


def test_capability_report_cli_runs_without_bmc_credentials():
    """capability-report is local and does not require endpoint credentials."""
    env = os.environ.copy()
    for name in (
            "REDFISH_IP",
            "REDFISH_USERNAME",
            "REDFISH_PASSWORD",
            "IDRAC_IP",
            "IDRAC_USERNAME",
            "IDRAC_PASSWORD",
    ):
        env.pop(name, None)

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "redfish_ctl.redfish_main",
            "--json_only",
            "--nocolor",
            "capability-report",
            "--vendor",
            "dell",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["data"]["vendors"]["dell"]["job_scheduling"] is True
