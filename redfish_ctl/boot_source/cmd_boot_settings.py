"""iDRAC boot settings query

This resource is used to represent the Boot Sources pending configuration
and related resources to clear pending and navigation to Jobs resource.

Example.
redfish_ctl boot-settings

Author Mus spyroot@gmail.com
"""
import argparse
import asyncio

from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed
from ..cmd_exceptions import InvalidArgument
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import RedfishApiRespond, Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class BootSettings(RedfishManagerBase,
                   scm_type=ApiRequestType.BootSettingsQuery,
                   name='boot_settings_query',
                   metaclass=Singleton):
    """
    Command enable boot option
    """

    def __init__(self, *args, **kwargs):
        """Initialize the boot-settings command."""
        super(BootSettings, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = argparse.ArgumentParser(add_help=False)
        cmd_parser.add_argument(
            '--async', default=False, action='store_true',
            required=False, dest="do_async",
            help="Will create a task and will not wait.")

        cmd_parser.add_argument(
            '-f', '--filename', required=False,
            default="", type=str,
            help="filename if we need to save a respond to a file.")

        help_text = "command fetch the boot setting and pending"
        return cmd_parser, "boot-settings", help_text

    def execute(self,
                filename: Optional[str] = None,
                do_async: Optional[bool] = False,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Query information for boot source settings and pending changes.

        :param do_async: note async will subscribe to an event loop.
        :param verbose: enable verbose output.
        :param filename: if filename indicate call will save a bios setting to a file.
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        # DellBootSources is a Dell OEM resource; degrade gracefully off Dell.
        target_api = f"{self.idrac_manage_servers}/Oem/Dell/DellBootSources/Settings"
        r = f"{self._default_method}{self.idrac_ip}{target_api}"

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
        except Exception:
            return CommandResult(
                {}, None, None,
                "DellBootSources is not available on this host (Dell-specific)")
        save_if_needed(filename, data)

        actions = self.discover_redfish_actions(self, data)
        return CommandResult(data, actions, None, None)
