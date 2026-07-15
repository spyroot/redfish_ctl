"""Firmware inventory command.

Command provides the option to retrieve firmware setting from a Redfish endpoint and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command caller can save
to a file and consume asynchronously or synchronously.

Example::

    redfish_ctl firmware_inventory
    redfish_ctl firmware_inventory --filename fw.json

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class FirmwareInventoryQuery(RedfishManagerBase,
                             scm_type=ApiRequestType.FirmwareInventoryQuery,
                             name='firmware_inv_query',
                             metaclass=Singleton):
    """
    Command implementation to get current firmware version from a Redfish endpoint.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the firmware_inventory command."""
        super(FirmwareInventoryQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the firmware_inventory subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_arg = argparse.ArgumentParser(add_help=False)
        cmd_arg.add_argument('--async', action='store_true', required=False, dest="do_async",
                             default=False, help="Will do async request.")

        cmd_arg.add_argument('--deep', action='store_true', required=False, dest="do_deep",
                             default=False, help="deep view to each pci.")

        cmd_arg.add_argument('-f', '--filename', required=False, type=str,
                             default="",
                             help="filename if we need to save a respond to a file.")

        help_text = "command fetch the firmware inventory view"
        return cmd_arg, "firmware_inventory", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                do_deep: Optional[bool] = False,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False, **kwargs) -> CommandResult:
        """Query firmware from a Redfish endpoint
        :param do_deep: will return verbose output for each pci device.
        :param do_async: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param filename: if filename indicate call will save respond to a file.
        :param data_type: a data serialized back.
        :return: in data type json will return json
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)
        r = f"{self._default_method}{self.idrac_ip}/redfish/v1/UpdateService/" \
            f"FirmwareInventory?$expand=*($levels=1)"
        response = self.api_get_call(r, headers)
        data = response.json()
        self.default_error_handler(response)
        save_if_needed(filename, data)
        return CommandResult(data, None, None, None)
