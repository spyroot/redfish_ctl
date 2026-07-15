"""Boot query command.

    redfish_ctl boot
    redfish_ctl boot --deep --filename boot.json

Command provides the option to retrieve boot source from a Redfish endpoint and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command
caller can save to a file and consume asynchronously or synchronously.

Author Mus spyroot@gmail.com
"""
import argparse
import asyncio
from abc import abstractmethod
from typing import Optional

from .cmd_exceptions import ResourceNotFound
from .cmd_utils import find_ids, save_if_needed
from .redfish_manager_base import RedfishManagerBase
from .redfish_manager_shared import ApiRequestType, Singleton
from .redfish_manager import CommandResult


class BootQuery(RedfishManagerBase,
                scm_type=ApiRequestType.BootQuery,
                name='boot_query',
                metaclass=Singleton):
    """
    Command return boot source
    """

    def __init__(self, *args, **kwargs):
        """Initialize the boot command."""
        super(BootQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_arg = argparse.ArgumentParser(add_help=False)
        cmd_arg.add_argument('--async', action='store_true',
                             required=False, dest="do_async",
                             default=False,
                             help="Will use asyncio.")

        cmd_arg.add_argument('-f', '--filename', required=False, type=str,
                             default="",
                             help="filename if we need to save a respond to a file.")

        cmd_arg.add_argument('--deep', action='store_true', required=False, dest="do_deep",
                             default=False, help="deep walk. will make a separate "
                                                 "rest call for each discovered api.")

        help_text = "command fetch the boot source"
        return cmd_arg, "boot", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                do_deep: Optional[bool] = False,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Query boot source from a Redfish endpoint
        :param do_async: will use asyncio
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_deep:
        :param filename: if filename indicate call will save the response to this file.
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        # Dell exposes a proprietary BootSources collection; standard Redfish
        # (Supermicro/OpenBMC, HPE) does not, so BootSources 404s there. Try Dell's
        # path, then fall back to the ComputerSystem's standard Boot object.
        r = f"{self._default_method}{self.idrac_ip}{self.idrac_manage_servers}/BootSources"

        try:
            if not do_async:
                response = self.api_get_call(r, headers)
                self.default_error_handler(response)
            else:
                loop = asyncio.get_event_loop()
                response = loop.run_until_complete(
                    self.api_async_get_until_complete(r, headers)
                )
            data = response.json()
        except ResourceNotFound:
            system = self.base_query(
                self.idrac_manage_servers, do_async=do_async).data or {}
            data = system.get("Boot", system)

        save_if_needed(filename, data)

        # extra data
        extra_actions = find_ids(data, "@odata.id")
        extra_data = None
        if do_deep:
            extra_data = [self.api_get_call(f"{self._default_method}{self.idrac_ip}{a}", headers).json()
                          for a in extra_actions]

        return CommandResult(data, None, extra_data, None)
