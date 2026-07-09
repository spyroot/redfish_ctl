"""iDRAC attribute command

Command provides the option to retrieve the iDRAC attribute and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command caller can save
to a file and consume asynchronously or synchronously.

python idrac_ctl.py --json attribute --filter ServerPwrMon.1.PeakCurrentTime

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import find_ids
from ..cmd_utils import save_if_needed
from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishJson


class AttributesQuery(
    IDracManager,
    scm_type=ApiRequestType.AttributesQuery,
    name='attribute_inventory',
    metaclass=Singleton):
    """iDRAC Attribute Query Command, fetch attribute data, caller can save to a file
    or output to a file or pass downstream.
    """

    def __init__(self, *args, **kwargs):
        super(AttributesQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args
        :param cls:
        :return:
        """
        cmd_arg = argparse.ArgumentParser(add_help=False)

        cmd_arg.add_argument(
            '--async', action='store_true', required=False, dest="do_async",
            default=False, help="Will create a task and will not wait.")

        cmd_arg.add_argument(
            '--deep', action='store_true', required=False, dest="do_deep",
            default=False, help="deep walk. will make a separate "
                                "REST call for each rest api.")

        cmd_arg.add_argument(
            '--attr_only', action='store_true', default=False,
            required=False, dest='attr_only',
            help="will show only attributes.")

        cmd_arg.add_argument(
            '--filter', required=False, type=str, dest="attr_filter",
            help="Filter on bios attribute information. "
                 "Example --filter ProcCStates , "
                 "will filter and and show C-State.")

        cmd_arg.add_argument(
            '-f', '--filename', required=False, type=str,
            default="",
            help="filename if we need to save a respond to a file.")

        help_text = "command fetch the attribute view"
        return cmd_arg, "attr", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                do_deep: Optional[bool] = False,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                attr_only: Optional[bool] = False,
                attr_filter: Optional[str] = "",
                **kwargs) -> CommandResult:
        """Queries attributes from iDRAC.
        :param attr_filter: filter on specific attribute.
        :param attr_only:
        :param do_async:
        :param verbose:
        :param do_deep:
        :param filename: if filename indicate call will save a bios setting to a file.
        :param data_type:
        :return:
        :raise: AuthenticationFailed, UnexpectedResponse
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        r = "/redfish/v1/Managers/System.Embedded.1/Attributes"
        cmd_result = self.base_query(r, do_async=do_async, do_expanded=do_deep)

        # filter
        data = self.filter_attribute(self, cmd_result.data, attr_filter)
        extra_actions = find_ids(data, "@odata.id")
        extra_data = None

        if do_deep:
            extra_data = [
                self.api_get_call(
                    f"{self._default_method}{self.idrac_ip}{a}", headers).json()
                for a in extra_actions
            ]

        if attr_only:
            data = data[RedfishJson.Attributes]

        save_if_needed(filename, data)
        return CommandResult(data, None, extra_data, None)
