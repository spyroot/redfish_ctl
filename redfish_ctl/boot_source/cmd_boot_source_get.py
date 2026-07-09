"""iDRAC query boot source device.

This cmd return Dell Boot Sources configuration for
a particular boot source.

Example.
idrac_ctl boot-source --dev NIC.Slot.8-1

Author Mus spyroot@gmail.com
"""
import asyncio

from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed
from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond, Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class BootSource(IDracManager,
                 scm_type=ApiRequestType.QueryBootOption,
                 name='boot_source_query',
                 metaclass=Singleton):
    """
    Command fetch boot option for particular device.
    """

    def __init__(self, *args, **kwargs):
        super(BootSource, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_reboot=True, is_file_save=True)
        # idrac_ctl.py boot-source-get --dev NIC.Slot.8-1

        cmd_parser.add_argument(
            '--dev', required=False, dest="boot_source",
            type=str, default=None, metavar="DEVICE",
            help="fetch verbose information for a device. "
                 "Example --dev NIC.Slot.8-1")

        help_text = "command fetch the boot source for device/devices"
        return cmd_parser, "boot-source", help_text

    def execute(self,
                boot_source: Optional[str] = None,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Query information for particular boot source device from idrac.
        Example python idrac_ctl.py boot-source-get --dev "HardDisk.List.1-1"

        :param boot_source: a device HardDisk.List.1-1
        :param do_async: note async will subscribe to an event loop.
        :param verbose:
        :param data_type: json or xml
        :param filename: if filename indicate call will save a bios setting to a file.
        :return: CommandResult and if filename provide will save to a file.
        """
        if verbose:
            self.logger.debug(f"cmd args"
                              f"data_type:{data_type} "
                              f"boot_source:{boot_source} "
                              f"do_async:{do_async} "
                              f"filename:{filename}")
            self.logger.debug(f"the rest of args: {kwargs}")

        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        cmd_result = self.sync_invoke(
            ApiRequestType.BootOptions, "boot_sources_query"
        )

        boot_data_sources = {}
        for full_dev_path in cmd_result.data:
            r = f"https://{self.idrac_ip}{full_dev_path}?$expand=*($levels=1)"
            if not do_async:
                response = self.api_get_call(r, headers)
                self.default_error_handler(response)
            else:
                loop = asyncio.get_event_loop()
                response = loop.run_until_complete(
                    self.api_async_get_until_complete(r, headers)
                )

            dev_data = response.json()
            devs = full_dev_path.split("/")
            if len(devs) > 0:
                dev = devs[-1]
                # add all by default
                if boot_source is None:
                    boot_data_sources[dev] = dev_data
                elif boot_source is not None and boot_source.lower() in dev.lower():
                    boot_data_sources[dev] = dev_data
                    break

        save_if_needed(filename, boot_data_sources)
        return CommandResult(boot_data_sources, None, None, None)
