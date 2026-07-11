"""iDRAC enable boot options.

This cmd return Dell Boot Sources Configuration and the related
resources.

Command provides the option to retrieve boot source from iDRAC and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command
caller can save to a file and consume asynchronously or synchronously.


Author Mus spyroot@gmail.com
"""

from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, BootSourceOverrideMode, IdracApiRespond, Singleton
from ..redfish_manager import CommandResult


class BootOneShot(IDracManager,
                  scm_type=ApiRequestType.BootOneShot,
                  name='boot_one_shot',
                  metaclass=Singleton):
    """
    Command enable boot option
    """

    def __init__(self, *args, **kwargs):
        super(BootOneShot, self).__init__(*args, **kwargs)

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
        """Query information for particular boot source device from idrac.
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
        :param filename: if filename indicate call will save a bios setting to a file.
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

        # query for a power state
        current_boot = self.sync_invoke(
            ApiRequestType.CurrentBoot,
            "current_boot_query"
        )
        boot_device = current_boot.data[
            'BootSourceOverrideTarget@Redfish.AllowableValues'
        ]
        if device not in boot_device:
            raise InvalidArgument(
                f"Invalid boot device {device}, "
                f"supported device {boot_device}"
            )

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

        # One-time boot must ARM the override, not just name a target: Redfish
        # ignores BootSourceOverrideTarget unless BootSourceOverrideEnabled is set.
        # "None" clears the override (Disabled); any real device arms it once.
        override_enabled = "Disabled" if device == "None" else "Once"

        payload = {
            "Boot": {
                "BootSourceOverrideEnabled": override_enabled,
                "BootSourceOverrideTarget": device,
                "BootSourceOverrideMode": mode,
                "UefiTargetBootSourceOverride": uefi_target
            }
        }

        # r = f"{self._default_method}{self.idrac_ip}/{self.idrac_manage_servers}"
        for key, value in dict(payload['Boot']).items():
            if value is None:
                del payload['Boot'][key]

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
            self.sync_invoke(
                ApiRequestType.ChassisReset, "reboot",
                reset_type="On"
            )

        cmd_result, api_resp = self.base_patch(
            self.idrac_manage_servers, payload=payload,
            do_async=do_async
        )

        if api_resp == IdracApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            cmd_result.data['task_id'] = task_id
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state

        if do_reboot:
            reboot_result = self.reboot(do_watch=True)
            cmd_result.data["reboot"] = reboot_result.data

        return cmd_result
