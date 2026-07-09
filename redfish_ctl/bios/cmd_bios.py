"""iDRAC bios command

Command provides the option to retrieve BIOS setting from iDRAC  and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command caller can save
to a file and consume asynchronously or synchronously.

idrac_ctl --json bios --filter SystemModelName
idrac_ctl --json bios --filter systemmodelname
idrac_ctl --json bios --filter ProcCStates

Piping to JQ.
idrac_ctl --nocolor bios --filter ProcCStates,SysMemSize | jq '.data'

Filter by multiple attributes, note you search done case in sensitive

idrac_ctl --json bios --filter systemmodelname,ProcCStates
{
    "ProcCStates": "Disabled",
    "systemmodelname": "PowerEdge R740"
}

On time boot

"OneTimeBootMode": "Disabled",
"BootMode": "Bios",

Author Mus spyroot@gmail.com
"""
import asyncio
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed, find_ids, from_json_spec
from ..custom_argparser.customer_argdefault import BiosSubcommand
from ..custom_argparser.customer_argdefault import CustomArgumentDefaultsHelpFormatter
from ..idrac_manager import IDracManager
from ..idrac_shared import IDRAC_API
from ..idrac_shared import IDRAC_JSON
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class BiosQuery(IDracManager,
                scm_type=ApiRequestType.BiosQuery,
                name='bios_inventory',
                metaclass=Singleton):
    """Bios Query Command, fetch bios data, caller can save to a file
    or output to a file or pass downstream.
    """

    def __init__(self, *args, **kwargs):
        super(BiosQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls, parent=None):
        """Registers command args
        :return:
        """
        if parent is not None:
            cmd_arg = BiosSubcommand(
                add_help=False,
                parents=[parent],
                description="query iDRAC bios information",
                formatter_class=CustomArgumentDefaultsHelpFormatter
            )
        else:
            cmd_arg = BiosSubcommand(
                add_help=False,
                description="query iDRAC bios information",
                formatter_class=CustomArgumentDefaultsHelpFormatter
            )

        cmd_arg.add_argument(
            '--async', required=False, default=False,
            action='store_true', dest="do_async",
            help="Will use asyncio.")

        cmd_arg.add_argument(
            '--attr_only', required=False, default=False,
            action='store_true', dest='attr_only',
            help="Will only show attributes.")

        cmd_arg.add_argument(
            '--filter', required=False, type=str, dest="attr_filter",
            metavar="BIOS_ATTRIBUTE",
            help="will filter on bios attribute information. "
                 "Example --filter ProcCStates , "
                 "will filter and and show C-State."
                 "You can pass a list. --filter ProcCStates,SysMemSize")

        cmd_arg.add_argument(
            '--from_file', required=False, type=str, dest="attr_filter_file",
            metavar="FILENAME",
            help="will user json file to filter a bios attribute information. "
                 "Example --from_file value_we_need.json. A file must "
                 "a JSON array")

        cmd_arg.add_argument(
            '--deep', default=False, required=False, action='store_true',
            dest="do_deep", help="deep walk. will make a separate "
                                 "rest api call for each discovered api.")

        cmd_arg.add_argument(
            '-f', '--filename', required=False, default="",
            type=str,
            help="filename if we need to save a respond to a file.")

        help_text = "command fetch the bios information"
        return cmd_arg, "bios", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                do_deep: Optional[bool] = False,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                attr_only: Optional[bool] = False,
                attr_filter: Optional[str] = "",
                attr_filter_file: Optional[str] = "",
                **kwargs) -> CommandResult:
        """Query bios settings from iDRAC.

        It supports filtering via coma separate list bios attributes
        or caller can pass a json file
        each attribute read form JSON file that contains a list of keys
        python idrac_ctl.py --nocolor bios --from_file specs/bios_query.json

        :param attr_filter: Filters by BIOS attributes. Each value is a JSON key.
                            If we need a query on multiply param, attr_filter must have a
                            comma-separated list of strings "ProcCStates,SysMemSize"
        :param attr_filter_file: A JSON file that container JSON keys we will filter on
        :param attr_only: Will only output bios attribute i.e. current bios settings
        :param verbose: enables a verbose output, mainly for debug.
        :param do_deep: deep walk
        :param do_async: will use asyncio
        :param data_type: default json
        :param filename: if filename signals  data must save to file, a bios setting to a file.
        :return:
        """
        from_file = False
        idrac_api = f"{self.idrac_manage_servers}{IDRAC_API.BIOS}"

        if verbose:
            self.logger.debug(
                f"cmd args data_type: {data_type} "
                f"do_deep:{do_deep} do_async:{do_async} "
                f"attr_filter:{attr_filter}")
            self.logger.debug(
                f"the rest of args: {kwargs}")

        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        r: str = f"{self._default_method}{self.idrac_ip}{idrac_api}"
        if not do_async:
            response = self.api_get_call(r, headers)
            self.default_error_handler(response)
        else:
            loop = asyncio.get_event_loop()
            response = loop.run_until_complete(
                self.api_async_get_until_complete(r, headers)
            )

        data = response.json()
        # list of action for bios
        action_dict = self.discover_redfish_actions(self, data)
        if attr_only is True and IDRAC_JSON.Attributes in data:
            data = {
                IDRAC_JSON.Attributes:
                    data[IDRAC_JSON.Attributes]
            }

        if attr_filter_file is not None and len(attr_filter_file) > 0:
            data_filter = from_json_spec(attr_filter_file)
            if isinstance(data_filter, list):
                attr_filter = ",".join(data_filter)
                data = self.filter_attribute(self, data, attr_filter)
            if isinstance(data_filter, str):
                data = self.filter_attribute(self, data, attr_filter)
            if isinstance(data_filter, dict):
                attr_keys = attr_filter.keys()
                attr_filter = ",".join(attr_keys)
                data = self.filter_attribute(self, data, attr_filter)
        else:
            data = self.filter_attribute(self, data, attr_filter)

        # save data
        save_if_needed(filename, data)

        # search for value
        extra_data_dict = {}
        if do_deep:
            api_links = find_ids(data, IDRAC_JSON.Data_id)
            api_links = [u for u in api_links if idrac_api != u]
            for api_link in api_links:
                r = f"{self._default_method}{self.idrac_ip}{api_link}"
                if not do_async:
                    response = self.api_get_call(r, headers)
                    self.default_error_handler(response)
                else:
                    loop = asyncio.get_event_loop()
                    response = loop.run_until_complete(
                        self.api_async_get_until_complete(r, headers)
                    )
                extra_data_dict[api_link] = response.json()

        for d in extra_data_dict.values():
            act = self.discover_redfish_actions(self, d)
            action_dict.update(act)

        return CommandResult(data, action_dict, extra_data_dict, None)
