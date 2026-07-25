"""Query the current boot configuration.

    redfish_ctl current_boot
    redfish_ctl current_boot --filename current_boot.json

This cmd return Dell Boot Sources Configuration and the related
resources.

Command provides the option to retrieve boot source from a Redfish endpoint and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command
caller can save to a file and consume asynchronously or synchronously.


Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from .idrac_manager import IDracManager
from .idrac_shared import ApiRequestType, Singleton
from .redfish_manager import CommandResult


class GetCurrentBoot(IDracManager,
                     scm_type=ApiRequestType.CurrentBoot,
                     name='current_boot_query',
                     metaclass=Singleton):
    """
    Command enable boot option
    """
    cmd_name = "current_boot_query"

    def __init__(self, *args, **kwargs):
        """Initialize the current_boot command."""
        super(GetCurrentBoot, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = argparse.ArgumentParser(add_help=False)
        cmd_parser.add_argument('--async', action='store_true', required=False, dest="do_async",
                                default=False, help="Will create a task and will not wait.")

        cmd_parser.add_argument('-f', '--filename', required=False, type=str,
                                default="",
                                help="filename if we need to save a respond to a file.")

        help_text = "command fetch the boot source for device/devices"
        return cmd_parser, "current_boot", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Query information for boot settings.
        Example python redfish_ctl.py get_boot_source --dev "HardDisk.List.1-1"

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

        r = f"{self._default_method}{self.idrac_ip}{self.idrac_manage_servers}"
        response = self.api_get_call(r, headers)
        self.default_error_handler(response)
        data = response.json()
        if 'Boot' in data:
            data = data['Boot']

        return CommandResult(data, None, None, None)
