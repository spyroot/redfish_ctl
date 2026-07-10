"""Tests for the read-only BIOS profile catalog command."""

import json
import os
import subprocess
import sys
from pathlib import Path

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_bios_profile_list_returns_committed_profile_rows(
        redfish_mock, redfish_service):
    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosProfile,
        "bios-profile",
        action="list",
    )

    assert isinstance(result, CommandResult)
    assert result.discovered is None
    assert result.extra is None
    assert result.error is None
    json.dumps(result.data, sort_keys=True)

    rows_by_name = {row["name"]: row for row in result.data}
    assert set(rows_by_name) >= {
        "dell-cstates-off",
        "gb300-extended-gpu-memory",
        "gb300-power-capped",
    }
    assert rows_by_name["gb300-power-capped"] == {
        "name": "gb300-power-capped",
        "vendor": "supermicro",
        "model": "GB300",
        "description": (
            "Enforce BMC input power capping on a 1-second timescale instead "
            "of the 50 ms default, smoothing power draw for rack-level power "
            "budgeting."
        ),
        "risk": "medium",
    }
    assert redfish_service.requests == []


def test_bios_profile_show_returns_full_profile(redfish_mock):
    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosProfile,
        "bios-profile",
        action="show",
        profile_name="gb300-extended-gpu-memory",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["name"] == "gb300-extended-gpu-memory"
    assert result.data["vendor"] == "supermicro"
    assert result.data["model"] == "GB300"
    assert result.data["risk"] == "medium"
    assert result.data["attributes"] == {"EGM": True}


def test_bios_profile_missing_directory_lists_empty(redfish_mock, tmp_path):
    missing_dir = tmp_path / "missing-profiles"

    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosProfile,
        "bios-profile",
        action="list",
        profile_dir=missing_dir,
    )

    assert isinstance(result, CommandResult)
    assert result.data == []
    assert result.error is None


def test_bios_profile_cli_runs_without_bmc_credentials():
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
            "bios-profile",
            "show",
            "dell-cstates-off",
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
    assert payload["data"]["name"] == "dell-cstates-off"
    assert payload["data"]["attributes"] == {"ProcCStates": "Disabled"}
