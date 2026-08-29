"""System view command.

Command provides the option to retrieve system view from a Redfish endpoint and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest of commands
caller can save to a file and consume asynchronously or synchronously.

    redfish_ctl system
    redfish_ctl system --deep

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed
from ..redfish_api_common import ApiRequestType, Singleton
from ..redfish_manager import CommandResult, RedfishManager


class SystemQuery(RedfishManager,
                  scm_type=ApiRequestType.SystemQuery,
                  name='system_query',
                  metaclass=Singleton):
    """This main compute system query rest call.

    By default, will output system view without going deeper.
    In case caller provide do_deep will execute each respected rest_api
    and aggregate result.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the system command."""
        super(SystemQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register commands args
        :param cls:
        :return:
        """
        cmd_parser = argparse.ArgumentParser(add_help=False)
        cmd_parser.add_argument('--sub_option', action='store_true',
                                required=False, dest='module',
                                help="fetch main compute system view.")
        # --deep sub-command.
        cmd_parser.add_argument('--deep', action='store_true', required=False, dest="do_deep",
                                default=False, help="deep walk. will make a separate "
                                                    "REST call for each rest api.")

        cmd_parser.add_argument('-f', '--filename', required=False, type=str,
                                default="",
                                help="filename if we need to save a respond to a file.")

        cmd_parser.add_argument('-s', '--save_all', required=False, type=str, dest="do_save",
                                default=False, help="for deep walk by default we don't "
                                                    "save result to a file. save_all "
                                                    "will save to a separate file.")

        cmd_parser.add_argument('--save_dir', required=False, type=str, dest="save_dir",
                                default=False, help="will save json files in separate directory.")

        cmd_parser.add_argument('--async', action='store_true', required=False, dest="do_async",
                                default=False, help="Will create a task and will not wait.")

        help_text = "command fetch the system view."
        return cmd_parser, "system", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                do_deep: Optional[bool] = False,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                save_dir: Optional[str] = None, **kwargs) -> CommandResult:
        """Read the managed ComputerSystem and its linked resources on request.

        The base manager resolves ``/redfish/v1/Systems/<managed-system-id>``.
        With ``do_deep``, each top-level ``@odata.id`` link is fetched and
        returned with the ComputerSystem payload.

        :param filename: optional output filename.
        :param data_type: response serialization type.
        :param do_deep: fetch top-level linked resources when true.
        :param verbose: print request details when true.
        :param do_async: accepted for command compatibility.
        :param save_dir: optional directory for saved output.
        :param kwargs: additional command arguments.
        :return: command result containing the system data and linked resources.
        """
        if verbose:
            print(f"filename {filename} data type: {data_type} "
                  f"do_deep: {do_deep}, do_async: {do_async}, "
                  f"save_dir: {save_dir}")

        data = self.base_query(
            self.managed_system_uri,
            data_type=data_type,
            do_async=do_async,
            verbose=verbose,
        ).data
        save_if_needed(filename, data, save_dir=save_dir)

        rest_endpoints = {}
        extra_data_dict = {}

        for k in data.keys():
            if isinstance(data[k], dict) and "@odata.id" in data[k]:
                sub_rest = data[k]["@odata.id"]
                rest_endpoints[k] = sub_rest
                # deep walk
                if do_deep:
                    extra_data_dict[k] = self.base_query(
                        sub_rest,
                        data_type=data_type,
                        do_async=do_async,
                        verbose=verbose,
                    ).data

        return CommandResult(data, rest_endpoints, extra_data_dict, None)
