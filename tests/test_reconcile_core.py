"""Tests for desired-state reconciliation planning and guarded apply."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

from redfish_ctl.base_manager import CommandBase
from redfish_ctl.command_shared import ApiRequestType
from redfish_ctl.reconcile import DesiredState, reconcile
from redfish_ctl.redfish_manager import CommandResult

GB300_CORPUS = corpus_dir(
    Path(__file__).parent / "supermicro_gb300_corpus.tar.gz", "172.25.230.37"
)
GB300_INDEX = {path.name.lower(): path for path in GB300_CORPUS.glob("*.json")}


class RecordingManager:
    """Return configured command payloads and record every facade call."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def sync_invoke(self, api_call, name, **kwargs):
        self.calls.append((api_call, name, kwargs))
        return self.results[(api_call, name, self._call_key(kwargs))]

    @staticmethod
    def _call_key(kwargs):
        return tuple(sorted((key, _freeze(value)) for key, value in kwargs.items()))


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _key(**kwargs):
    return RecordingManager._call_key(kwargs)


def test_desired_state_rejects_boolean_reboot_shortcut():
    """A reboot request must name the reset type instead of using truthy shorthand."""
    with pytest.raises(ValueError, match="reboot.resetType"):
        DesiredState.from_mapping({"reboot": True})


def test_reconcile_skips_ntp_when_manager_already_matches():
    """Converged ManagerNetworkProtocol NTP state must not issue an apply call."""
    manager = RecordingManager({
        (
            ApiRequestType.ManagerNetworkProtocol,
            "manager-network",
            _key(),
        ): CommandResult(
            [
                {
                    "Manager": "BMC_0",
                    "NTP": {
                        "ProtocolEnabled": True,
                        "NTPServers": ["0.pool.ntp.org"],
                    },
                }
            ],
            None,
            None,
            None,
        )
    })

    result = reconcile(
        manager,
        DesiredState(
            ntp_servers=("0.pool.ntp.org",),
            ntp_manager_id="BMC_0",
        ),
        confirm=True,
    )

    assert [(step.kind, step.required) for step in result.steps] == [
        ("ntp", False),
    ]
    assert result.applied == ()
    assert manager.calls == [
        (ApiRequestType.ManagerNetworkProtocol, "manager-network", {}),
    ]


def test_reconcile_skips_boot_when_one_time_override_already_matches():
    """Converged one-time boot state must not call the mutating boot command."""
    manager = RecordingManager({
        (
            ApiRequestType.CurrentBoot,
            "current_boot_query",
            _key(),
        ): CommandResult(
            {
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "Pxe",
                "BootSourceOverrideMode": "UEFI",
            },
            None,
            None,
            None,
        )
    })

    result = reconcile(
        manager,
        DesiredState(boot_device="Pxe", boot_mode="UEFI"),
        confirm=True,
    )

    assert [(step.kind, step.required) for step in result.steps] == [
        ("boot", False),
    ]
    assert result.applied == ()
    assert manager.calls == [
        (ApiRequestType.CurrentBoot, "current_boot_query", {}),
    ]


def _fixture_for_path(path: str) -> Path | None:
    name = "_" + path.strip("/").replace("/", "_") + ".json"
    return GB300_INDEX.get(name.lower())


def test_reconcile_dry_run_plans_without_mutating_commands():
    """Dry-run may read and preview, but it must not apply BIOS or boot changes."""
    diff_payload = {
        "profile": {"name": "gb300-power-capped"},
        "matches": False,
        "summary": {"different": 1, "missing": 0, "matching": 2, "total": 3},
        "attributes": [
            {
                "attribute": "ServerPowerControl",
                "current": "Balanced",
                "desired": "PowerSaving",
                "status": "different",
            }
        ],
    }
    ntp_preview = {
        "dry_run": True,
        "servers": ["0.pool.ntp.org"],
        "plan": [
            {
                "Manager": "BMC_0",
                "target": "/redfish/v1/Managers/BMC_0/NetworkProtocol",
                "payload": {
                    "NTP": {
                        "NTPServers": ["0.pool.ntp.org"],
                        "ProtocolEnabled": True,
                    }
                },
            }
        ],
        "skipped": [],
    }
    reboot_preview = {
        "dry_run": True,
        "target": "/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset",
        "payload": {"ResetType": "GracefulRestart"},
    }
    manager = RecordingManager({
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            _key(action="diff", profile_name="gb300-power-capped"),
        ): CommandResult(diff_payload, None, None, None),
        (
            ApiRequestType.NtpSet,
            "ntp-set",
            _key(
                servers=("0.pool.ntp.org",),
                manager_id="BMC_0",
                confirm=False,
            ),
        ): CommandResult(ntp_preview, None, None, None),
        (
            ApiRequestType.ManagerNetworkProtocol,
            "manager-network",
            _key(),
        ): CommandResult(
            [
                {
                    "Manager": "BMC_0",
                    "NTP": {
                        "ProtocolEnabled": True,
                        "NTPServers": [],
                    },
                }
            ],
            None,
            None,
            None,
        ),
        (
            ApiRequestType.CurrentBoot,
            "current_boot_query",
            _key(),
        ): CommandResult(
            {
                "BootSourceOverrideEnabled": "Disabled",
                "BootSourceOverrideTarget": "None",
                "BootSourceOverrideMode": "UEFI",
            },
            None,
            None,
            None,
        ),
        (
            ApiRequestType.ComputerSystemReset,
            "reboot",
            _key(
                reset_type="GracefulRestart",
                dry_run=True,
                do_wait=False,
                do_async=False,
            ),
        ): CommandResult(reboot_preview, None, None, None),
    })
    desired = DesiredState.from_mapping({
        "biosProfile": "gb300-power-capped",
        "ntp": {"servers": ["0.pool.ntp.org"], "manager": "BMC_0"},
        "boot": {"device": "Pxe", "mode": "UEFI"},
        "reboot": {"resetType": "GracefulRestart"},
    })

    result = reconcile(manager, desired)

    assert result.dry_run is True
    assert [(step.kind, step.required) for step in result.steps] == [
        ("bios-profile", True),
        ("ntp", True),
        ("boot", True),
        ("reboot", True),
    ]
    assert result.applied == ()
    assert manager.calls == [
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            {"action": "diff", "profile_name": "gb300-power-capped"},
        ),
        (
            ApiRequestType.ManagerNetworkProtocol,
            "manager-network",
            {},
        ),
        (
            ApiRequestType.NtpSet,
            "ntp-set",
            {
                "servers": ("0.pool.ntp.org",),
                "manager_id": "BMC_0",
                "confirm": False,
            },
        ),
        (
            ApiRequestType.CurrentBoot,
            "current_boot_query",
            {},
        ),
        (
            ApiRequestType.ComputerSystemReset,
            "reboot",
            {
                "reset_type": "GracefulRestart",
                "dry_run": True,
                "do_wait": False,
                "do_async": False,
            },
        ),
    ]


def test_reconcile_confirm_applies_only_required_changes():
    """Confirmed reconciliation applies planned changes through guarded commands."""
    diff_payload = {
        "profile": {"name": "gb300-power-capped"},
        "matches": False,
        "summary": {"different": 1, "missing": 0, "matching": 2, "total": 3},
    }
    apply_payload = {
        "profile": "gb300-power-capped",
        "dry_run": False,
        "staged": {"Attributes": {"ServerPowerControl": "PowerSaving"}},
    }
    ntp_apply = {
        "servers": ["0.pool.ntp.org"],
        "applied": [
            {
                "Manager": "BMC_0",
                "target": "/redfish/v1/Managers/BMC_0/NetworkProtocol",
                "status": "RedfishCommandRespond.Ok",
                "error": None,
            }
        ],
        "skipped": [],
    }
    boot_result = {"task_id": "JID_BOOT", "task_state": "Running"}
    reboot_result = {"task_id": "JID_RESET", "task_state": "Running"}
    manager = RecordingManager({
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            _key(action="diff", profile_name="gb300-power-capped"),
        ): CommandResult(diff_payload, None, None, None),
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            _key(
                action="apply",
                profile_name="gb300-power-capped",
                confirm=True,
                dry_run=False,
            ),
        ): CommandResult(apply_payload, None, None, None),
        (
            ApiRequestType.NtpSet,
            "ntp-set",
            _key(
                servers=("0.pool.ntp.org",),
                manager_id=None,
                confirm=True,
            ),
        ): CommandResult(ntp_apply, None, None, None),
        (
            ApiRequestType.ManagerNetworkProtocol,
            "manager-network",
            _key(),
        ): CommandResult(
            [
                {
                    "Manager": "BMC_0",
                    "NTP": {
                        "ProtocolEnabled": True,
                        "NTPServers": [],
                    },
                }
            ],
            None,
            None,
            None,
        ),
        (
            ApiRequestType.CurrentBoot,
            "current_boot_query",
            _key(),
        ): CommandResult(
            {
                "BootSourceOverrideEnabled": "Disabled",
                "BootSourceOverrideTarget": "None",
            },
            None,
            None,
            None,
        ),
        (
            ApiRequestType.BootOneShot,
            "boot_one_shot",
            _key(device="Pxe", mode=None, uefi_target=None, do_reboot=False),
        ): CommandResult(boot_result, None, None, None),
        (
            ApiRequestType.ComputerSystemReset,
            "reboot",
            _key(
                reset_type="GracefulRestart",
                dry_run=False,
                do_wait=True,
                do_async=False,
            ),
        ): CommandResult(reboot_result, None, None, None),
    })
    desired = DesiredState(
        bios_profile="gb300-power-capped",
        ntp_servers=("0.pool.ntp.org",),
        boot_device="Pxe",
        reset_type="GracefulRestart",
    )

    result = reconcile(manager, desired, confirm=True, wait_for_reboot=True)

    assert result.dry_run is False
    assert [(item.kind, item.changed) for item in result.applied] == [
        ("bios-profile", True),
        ("ntp", True),
        ("boot", True),
        ("reboot", True),
    ]
    assert manager.calls == [
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            {"action": "diff", "profile_name": "gb300-power-capped"},
        ),
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            {
                "action": "apply",
                "profile_name": "gb300-power-capped",
                "confirm": True,
                "dry_run": False,
            },
        ),
        (
            ApiRequestType.ManagerNetworkProtocol,
            "manager-network",
            {},
        ),
        (
            ApiRequestType.NtpSet,
            "ntp-set",
            {
                "servers": ("0.pool.ntp.org",),
                "manager_id": None,
                "confirm": True,
            },
        ),
        (
            ApiRequestType.CurrentBoot,
            "current_boot_query",
            {},
        ),
        (
            ApiRequestType.BootOneShot,
            "boot_one_shot",
            {
                "device": "Pxe",
                "mode": None,
                "uefi_target": None,
                "do_reboot": False,
            },
        ),
        (
            ApiRequestType.ComputerSystemReset,
            "reboot",
            {
                "reset_type": "GracefulRestart",
                "dry_run": False,
                "do_wait": True,
                "do_async": False,
            },
        ),
    ]


def test_reconcile_skips_matching_profile():
    """A matching BIOS profile is reported but not applied."""
    manager = RecordingManager({
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            _key(action="diff", profile_name="balanced"),
        ): CommandResult(
            {
                "profile": {"name": "balanced"},
                "matches": True,
                "summary": {"different": 0, "missing": 0, "matching": 3, "total": 3},
            },
            None,
            None,
            None,
        )
    })

    result = reconcile(manager, DesiredState(bios_profile="balanced"), confirm=True)

    assert [(step.kind, step.required) for step in result.steps] == [
        ("bios-profile", False),
    ]
    assert result.applied == ()
    assert manager.calls == [
        (
            ApiRequestType.BiosProfile,
            "bios-profile",
            {"action": "diff", "profile_name": "balanced"},
        )
    ]


def test_reconcile_dry_run_uses_gb300_corpus_without_writes():
    """Dry-run reconciliation uses registered commands without mutating requests."""
    requests_mock = pytest.importorskip("requests_mock")
    seen_methods: list[str] = []

    def get_cb(request, context):
        seen_methods.append(request.method)
        fixture = _fixture_for_path(request.path)
        if fixture is None:
            context.status_code = 404
            return json.dumps({"error": f"no fixture for {request.path}"})
        context.status_code = 200
        return fixture.read_text(encoding="utf-8")

    with requests_mock.Mocker() as mocker:
        mocker.get(requests_mock.ANY, text=get_cb)
        manager = CommandBase(
            idrac_ip="mock-gb300",
            idrac_username="root",
            idrac_password="mock",
            idrac_port=8080,
            insecure=True,
            is_http=True,
            is_debug=False,
        )
        result = reconcile(
            manager,
            DesiredState(
                ntp_servers=("0.pool.ntp.org",),
                reset_type="GracefulRestart",
            ),
        )

    assert result.dry_run is True
    assert [(step.kind, step.required) for step in result.steps] == [
        ("ntp", True),
        ("reboot", True),
    ]
    assert result.applied == ()
    assert seen_methods
    assert set(seen_methods) == {"GET"}
