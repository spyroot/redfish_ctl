"""iDRAC enable boot options.

Command provides the option to enable boot source,
so it is used during a boot process. BootSources settings,
require a system reset to apply.

For example.
idrac_ctl boot-source-enable --dev NIC.Slot.8-1 --enable yes

Example
idrac_ctl boot-source-enable --dev NIC.Slot.8-1 --enable yes --reboot

Enables boot on nic slot 8-1

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import str2bool
from ..idrac_shared import IdracApiRespond, ResetType
from ..cmd_utils import save_if_needed
from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond, Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..idrac_shared import IDRAC_API
from ..idrac_shared import IdracApiRespond


class EnableBootOptions(IDracManager,
                        scm_type=ApiRequestType.EnableBootOptions,
                        name='boot_enable',
                        metaclass=Singleton):
    """
    Command enable boot option
    """

    def __init__(self, *args, **kwargs):
        super(EnableBootOptions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_reboot=True, is_file_save=False)

        cmd_parser.add_argument(
            '--dev', required=True, dest="boot_source",
            type=str, default=None, metavar="DEVICE",
            help="fetch verbose information for a device. Example --dev NIC.Slot.8-1")

        cmd_parser.add_argument(
            '--enable', required=True, metavar="yes",
            dest="is_enabled", type=str2bool, nargs='?',
            help="Enable or Disable target boot device. yes|no, true|false")

        help_text = "command enable the boot on a particular device."
        return cmd_parser, "boot-source-enable", help_text

    def execute(self,
                boot_source: str,
                is_enabled: bool,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_reboot: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Enables boot source for a particular device.

        :param do_reboot:  reboots a host.
        :param boot_source: A device  Example, HardDisk.List.1-1 , NIC.Slot.8-1 etc
        :param is_enabled: enables or disables target boot device.
        :param do_async: will do async call
        :param verbose: enables verbose output.
        :param data_type: json or xml
        :param filename: if filename indicate call will save a bios setting to a file.
        :return: CommandResult and if filename provide will save to a file.
        """
        if verbose:
            self.logger.info(f"cmd args data_type: {data_type} "
                             f"boot_source:{boot_source}  is_enabled:{is_enabled}, "
                             f"do_async:{do_async} filename:{filename}")
            self.logger.info(f"the rest of args: {kwargs}")

        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        if boot_source is None:
            raise ValueError("Please indicate boot source.")

        dev_result = self.sync_invoke(
            ApiRequestType.BootOptions, "boot_sources_query"
        )

        target_dev = None
        boot_data_sources = []
        for full_dev_path in dev_result.data:
            devs = full_dev_path.split("/")
            if len(devs) > 0:
                dev = devs[-1]
                boot_data_sources.append(dev)
                if boot_source.lower() in dev.lower():
                    target_dev = full_dev_path
                    break

        if target_dev is None:
            print(f"Unknown dev. available boot source. {boot_data_sources}")
            return CommandResult(None, None, None, None)

        payload = {
            "BootOptionEnabled": bool(is_enabled)
        }

        cmd_result, api_resp = self.base_patch(
            target_dev, payload=payload,
            do_async=do_async
        )
        if api_resp == IdracApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        if do_reboot:
            self.reboot(
                do_watch=False if do_async else True,
                default_reboot_type=ResetType.PowerCycle.value
            )

        return cmd_result
