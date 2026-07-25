"""Query a one-shot boot source device.

This cmd return Dell Boot Sources Configuration and the related
resources.

Command provides the option to retrieve boot source from a Redfish endpoint and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command
caller can save to a file and consume asynchronously or synchronously.

Example.
redfish_ctl boot-one-shot --device Cd

Author Mus spyroot@gmail.com
"""

from abc import abstractmethod
from typing import Optional

import requests

from ..cmd_exceptions import (
    FailedDiscoverAction,
    InvalidArgument,
    UnexpectedResponse,
    UnsupportedAction,
)
from ..idrac_manager import IDracManager
from ..idrac_shared import (
    ApiRequestType,
    BootSourceOverrideMode,
    RedfishApiRespond,
    Singleton,
)
from ..redfish_exceptions import RedfishException
from ..redfish_manager import CommandResult

_BOOT_TARGET_ALIASES = {
    "Cd": ("Cd", "CD/DVD", "UsbCd", "UefiCd", "UefiUsbCd"),
}
_LEGACY_FLAT_BOOT_TARGETS = {
    "CD/DVD",
    "FloppyRemovableMedia",
    "UsbKey",
    "UsbHdd",
    "UsbFloppy",
}
_RESET_ERRORS = (
    FailedDiscoverAction,
    InvalidArgument,
    RedfishException,
    requests.exceptions.RequestException,
    UnexpectedResponse,
    UnsupportedAction,
)


class BootOneShot(IDracManager,
                  scm_type=ApiRequestType.BootOneShot,
                  name='boot_one_shot',
                  metaclass=Singleton):
    """
    Command enable boot option
    """

    def __init__(self, *args, **kwargs):
        """Initialize the boot-one-shot command."""
        super(BootOneShot, self).__init__(*args, **kwargs)

    @staticmethod
    def _resolve_boot_device(device: str, boot_devices: list[str]) -> str:
        """Resolve a requested boot target against live-advertised values.

        :param device: requested BootSourceOverrideTarget value.
        :param boot_devices: values advertised by the ComputerSystem Boot object.
        :return: the target value to send to the BMC.
        :raises InvalidArgument: when the target is unsupported.
        """
        if device in boot_devices:
            return device
        for candidate in _BOOT_TARGET_ALIASES.get(device, ()):
            if candidate in boot_devices:
                return candidate
        raise InvalidArgument(
            f"Invalid boot device {device}, "
            f"supported device {boot_devices}"
        )

    @staticmethod
    def _computer_system_version_at_most(
        system: Optional[dict],
        major: int,
        minor: int,
    ) -> bool:
        """Return whether a ComputerSystem @odata.type is at or below a version.

        :param system: full ComputerSystem resource body.
        :param major: maximum Redfish ComputerSystem major version.
        :param minor: maximum Redfish ComputerSystem minor version.
        :return: True when ``system`` declares a version at or below the limit.
        """
        if not isinstance(system, dict):
            return False
        type_name = str(system.get("@odata.type", ""))
        marker = "#ComputerSystem.v"
        if marker not in type_name:
            return False
        version = type_name.split(marker, 1)[1].split(".", 1)[0]
        parts = version.split("_")
        if len(parts) < 2:
            return False
        try:
            parsed = (int(parts[0]), int(parts[1]))
        except ValueError:
            return False
        return parsed <= (major, minor)

    @staticmethod
    def _use_legacy_flat_payload(
        boot_devices: list[str],
        system: Optional[dict],
    ) -> bool:
        """Return whether the endpoint expects top-level Boot fields.

        Older Supermicro X10 Redfish 1.0.1 systems advertise legacy-only boot
        target names such as ``CD/DVD`` and reject the newer nested
        ``{"Boot": ...}`` PATCH shape. Standard UEFI target names alone are not
        enough to identify this older shape.

        :param boot_devices: values advertised by the ComputerSystem Boot object.
        :param system: full ComputerSystem resource body.
        :return: True when the flat legacy PATCH shape should be used.
        """
        if not bool(set(boot_devices) & _LEGACY_FLAT_BOOT_TARGETS):
            return False
        if not isinstance(system, dict):
            return False
        manufacturer = str(system.get("Manufacturer", "")).lower()
        if "supermicro" not in manufacturer:
            return False
        return BootOneShot._computer_system_version_at_most(system, 1, 3)

    @staticmethod
    def _boot_payload(
        device: str,
        mode: Optional[str],
        uefi_target: Optional[str],
        boot_devices: list[str],
        system: Optional[dict],
    ) -> dict:
        """Build the one-shot boot PATCH payload for the endpoint generation.

        :param device: resolved BootSourceOverrideTarget value.
        :param mode: optional BootSourceOverrideMode value.
        :param uefi_target: optional UefiTargetBootSourceOverride value.
        :param boot_devices: live-advertised target values.
        :param system: full ComputerSystem resource body.
        :return: PATCH payload accepted by the endpoint generation.
        """
        override_enabled = "Disabled" if device == "None" else "Once"
        boot = {
            "BootSourceOverrideEnabled": override_enabled,
            "BootSourceOverrideTarget": device,
            "BootSourceOverrideMode": mode,
            "UefiTargetBootSourceOverride": uefi_target
        }
        for key, value in dict(boot).items():
            if value is None:
                del boot[key]
        if BootOneShot._use_legacy_flat_payload(boot_devices, system):
            boot.pop("BootSourceOverrideMode", None)
            boot.pop("UefiTargetBootSourceOverride", None)
            return boot
        return {"Boot": boot}

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_reboot=True, is_expanded=False)

        cmd_parser.add_argument(
            '--device', required=False, type=str,
            default="Cd",
            help="boot device Pxe,Cd,Hdd,BiosSetup,UefiTarget,SDCard etc")

        cmd_parser.add_argument(
            '--power_on', action='store_true',
            required=False, dest="do_power_on",
            help="will power on a chassis., if current state in power-down.")

        cmd_parser.add_argument(
            '--uefi_target', required=False, type=str,
            default=None,
            help="uefi_target")

        cmd_parser.add_argument(
            '--mode', required=False, type=str, default=None,
            choices=[m.value for m in BootSourceOverrideMode],
            help="boot source override mode UEFI or Legacy (Redfish "
                 "BootSourceOverrideMode). Force UEFI to boot the UEFI device "
                 "entry, e.g. a UEFI virtual CD/DVD on boards where the Legacy "
                 "path is unusable.")

        cmd_parser.add_argument(
            '--dry_run', action='store_true',
            required=False, dest="dry_run",
            default=False,
            help="preview the one-time boot PATCH payload; write nothing.")

        help_text = "command change one shoot boot"
        return cmd_parser, "boot-one-shot", help_text

    def execute(self,
                device: Optional[str] = None,
                uefi_target: Optional[str] = None,
                mode: Optional[str] = None,
                do_check: Optional[str] = None,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_reboot: Optional[bool] = False,
                do_power_on: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                confirm: Optional[bool] = True,
                **kwargs) -> CommandResult:
        """Query information for particular boot source device from a Redfish endpoint.
        Example python redfish_ctl.py get_boot_source --dev "HardDisk.List.1-1"

        VenHw(986D1755-B9D0-4F8D-A0DA-D1DB18672045)

        :param do_reboot:  will reboot host
        :param do_power_on: will power on server if server in power down state.
        :param dry_run: preview the PATCH payload without mutating Boot settings.
        :param confirm: allow the PATCH to fire. Defaults True for backward
                        compatibility; internal dry-run callers pass False.
        :param mode: boot source override mode, "UEFI" or "Legacy"
                     (Redfish BootSourceOverrideMode). None leaves it unchanged.
        :param uefi_target:
        :param device:  get the list of supported device.
                        For example None, Pxe,Cd,Hdd,BiosSetup,UefiTarget,SDCard,UefiHttp
        :param do_check:
        :param do_async: note async will subscribe to an event loop.
        :param verbose:
        :param filename: if filename indicate call will save the response to this file.
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        if verbose:
            self.logger.debug(f"cmd args data_type: {data_type} "
                              f"do_async:{do_async} filename:{filename}")
            self.logger.debug(f"the rest of args: {kwargs}")

        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        system_result = self.base_query(
            self.idrac_manage_servers,
            data_type=data_type,
            do_async=do_async,
            verbose=verbose,
        )
        system = system_result.data if isinstance(system_result.data, dict) else {}
        boot = system.get("Boot", system)
        boot_device = boot[
            'BootSourceOverrideTarget@Redfish.AllowableValues'
        ]
        device = self._resolve_boot_device(device, boot_device)

        # validate the requested boot mode (UEFI vs Legacy) before mutating
        valid_modes = [m.value for m in BootSourceOverrideMode]
        if mode is not None and mode not in valid_modes:
            raise InvalidArgument(
                f"Invalid boot mode {mode}, supported modes {valid_modes}"
            )

        if uefi_target is not None:
            current_boot = self.sync_invoke(
                ApiRequestType.BootOptions, "boot_sources_query"
            )
            uefi_devs = [d['UefiDevicePath'] for d
                         in current_boot.extra['Members']
                         if 'UefiDevicePath' in d]
            if uefi_target not in uefi_devs:
                raise InvalidArgument(
                    f"Invalid uefi device path {uefi_target},"
                    f" supported uefi devices {boot_device}")

        # record the one-time boot request so UEFI vs Legacy boots are auditable
        self.logger.info(
            "boot-one-shot: one-time boot target=%s mode=%s uefi_target=%s",
            device,
            mode if mode is not None else "(unchanged)",
            uefi_target if uefi_target is not None else "(none)",
        )

        payload = self._boot_payload(
            device,
            mode,
            uefi_target,
            boot_device,
            system,
        )

        if dry_run or not confirm:
            return CommandResult(
                {
                    "dry_run": True,
                    "target": self.idrac_manage_servers,
                    "payload": payload,
                    "blocked": None if dry_run else "one-time boot requires confirm",
                },
                None,
                None,
                None,
            )

        # power on first if a client requested and this is a confirmed write.
        if do_power_on:
            try:
                power_result = self.sync_invoke(
                    ApiRequestType.ChassisReset, "reboot",
                    reset_type="On"
                )
            except _RESET_ERRORS as exc:
                return CommandResult(
                    {"target": self.idrac_manage_servers, "payload": payload},
                    None,
                    None,
                    f"power-on pre-step failed: {exc}",
                )
            if power_result.error is not None:
                return power_result

        cmd_result, api_resp = self.base_patch(
            self.idrac_manage_servers, payload=payload,
            do_async=do_async
        )

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            cmd_result.data['task_id'] = task_id
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state

        if do_reboot:
            try:
                reboot_result = self.reboot(do_watch=True)
            except _RESET_ERRORS as exc:
                data = cmd_result.data if isinstance(cmd_result.data, dict) else {}
                data["reboot_error"] = str(exc)
                return CommandResult(
                    data,
                    cmd_result.discovered,
                    cmd_result.extra,
                    f"reboot post-step failed: {exc}",
                )
            if reboot_result.error is not None:
                data = cmd_result.data if isinstance(cmd_result.data, dict) else {}
                data["reboot"] = reboot_result.data
                return CommandResult(
                    data,
                    cmd_result.discovered,
                    cmd_result.extra,
                    reboot_result.error,
                )
            cmd_result.data["reboot"] = reboot_result.data

        return cmd_result
