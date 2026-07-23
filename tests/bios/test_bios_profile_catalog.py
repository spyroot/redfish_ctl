"""Tests for the BIOS profile catalog, diff, and guarded apply command."""

import json
import os
import subprocess
import sys
from pathlib import Path

from redfish_ctl.idrac_shared import ApiRequestType
from redfish_ctl.redfish_manager import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_bios_profile_diff_compares_profile_to_current_bios(
        redfish_mock, redfish_service, tmp_path):
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    (profile_dir / "dell-low-latency.json").write_text(json.dumps({
        "name": "dell-low-latency",
        "vendor": "dell",
        "model": "PowerEdge",
        "description": "Test profile that changes one current BIOS value.",
        "risk": "medium",
        "attributes": {
            "ProcCStates": "Enabled",
            "SriovGlobalEnable": "Enabled",
        },
    }))

    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosProfile,
        "bios-profile",
        action="diff",
        profile_name="dell-low-latency",
        profile_dir=profile_dir,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data == {
        "profile": {
            "name": "dell-low-latency",
            "vendor": "dell",
            "model": "PowerEdge",
            "risk": "medium",
        },
        "matches": False,
        "summary": {
            "total": 2,
            "matching": 1,
            "different": 1,
            "missing": 0,
        },
        "attributes": [
            {
                "attribute": "ProcCStates",
                "current": "Disabled",
                "desired": "Enabled",
                "status": "different",
            },
            {
                "attribute": "SriovGlobalEnable",
                "current": "Enabled",
                "desired": "Enabled",
                "status": "matching",
            },
        ],
    }
    assert redfish_service.requests
    assert {request.method for request in redfish_service.requests} == {"GET"}


def test_bios_profile_apply_defaults_to_dry_run_with_snapshot(
        redfish_mock, redfish_service):
    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosProfile,
        "bios-profile",
        action="apply",
        profile_name="dell-cstates-off",
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is True
    assert result.data["profile"] == "dell-cstates-off"
    assert result.data["change"] == {"Attributes": {"ProcCStates": "Disabled"}}
    assert result.data["rollback"] == {"Attributes": {"ProcCStates": "Disabled"}}
    assert result.data["staged"] == {
        "Attributes": {"ProcCStates": "Disabled"},
        "@Redfish.SettingsApplyTime": {"ApplyTime": "OnReset"},
    }
    assert [
        request
        for request in redfish_service.requests
        if request.method in {"PATCH", "POST", "DELETE"}
    ] == []


def test_bios_profile_apply_confirm_stages_bios_settings(
        redfish_mock, redfish_service):
    redfish_service._overlay[
        "/redfish/v1/Systems/System.Embedded.1/Bios/Settings"
    ] = {"Attributes": {}}
    redfish_service._overlay[
        "/redfish/v1/systems/system.embedded.1/bios/settings"
    ] = {"Attributes": {}}

    result = redfish_mock.sync_invoke(
        ApiRequestType.BiosProfile,
        "bios-profile",
        action="apply",
        profile_name="dell-cstates-off",
        confirm=True,
    )

    assert isinstance(result, CommandResult)
    assert result.error is None
    assert result.data["dry_run"] is False
    assert result.data["profile"] == "dell-cstates-off"
    assert result.data["change"] == {"Attributes": {"ProcCStates": "Disabled"}}
    assert result.data["rollback"] == {"Attributes": {"ProcCStates": "Disabled"}}

    patch_requests = [
        request for request in redfish_service.requests
        if request.method == "PATCH"
    ]
    assert len(patch_requests) == 1
    assert patch_requests[0].path.lower().endswith("/bios/settings")
    assert patch_requests[0].json() == {
        "Attributes": {"ProcCStates": "Disabled"},
        "@Redfish.SettingsApplyTime": {"ApplyTime": "OnReset"},
    }


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


def test_bios_profile_cli_accepts_canonical_connection_flags():
    """Canonical global connection flags parse without a live BMC."""
    env = os.environ.copy()
    for name in (
            "REDFISH_IP",
            "REDFISH_USERNAME",
            "REDFISH_PASSWORD",
            "REDFISH_PORT",
            "IDRAC_IP",
            "IDRAC_USERNAME",
            "IDRAC_PASSWORD",
            "IDRAC_PORT",
    ):
        env.pop(name, None)

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "redfish_ctl.redfish_main",
            "--host",
            "203.0.113.10",
            "--username",
            "root",
            "--password",
            "not-real",
            "--port",
            "443",
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


def test_bios_profile_diff_cli_requires_bmc_connection():
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
            "diff",
            "dell-cstates-off",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 1
    assert "Please indicate the Redfish host." in result.stdout


def test_bios_profile_apply_cli_requires_bmc_credentials():
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
            "apply",
            "dell-cstates-off",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 1
    assert "Please indicate the Redfish host." in result.stdout
