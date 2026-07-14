"""iDRAC raid service query

Command provides the option to retrieve raid service  from iDRAC
and serialize back as caller as JSON, YAML, and XML. In addition,
it automatically registers to the command line ctl tool. Similarly
to the rest command caller can save to a file and consume asynchronously
or synchronously.

In result command also store all discovered action.

For example.
{'AssignSpare': '/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellRaidService/Actions/DellRaidService.AssignSpare'}

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class RaidServiceQuery(RedfishManagerBase,
                       scm_type=ApiRequestType.RaidServiceQuery,
                       name='raid_service_query',
                       metaclass=Singleton):
    """Raid service query Command, fetch raid  service data, caller can save to a file
    or output to a file or pass downstream.
    """

    def __init__(self, *args, **kwargs):
        super(RaidServiceQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args
        :return:
        """
        cmd_parser = argparse.ArgumentParser(add_help=False)
        cmd_parser.add_argument('-f', '--filename', required=False, type=str,
                                default="",
                                help="filename if we need to save a respond to a file.")

        help_text = "command raid information"
        return cmd_parser, "raid", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Command query raid service and returns raid service in data field
        of Command Result. The discovered named tuple store action
        and respected rest APIs.

        :param do_async:
        :param verbose:
        :param filename: if filename indicate call will save a bios setting to a file.
        :param data_type:
        :return: return raid service
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        # DellRaidService is a Dell OEM resource; use the discovered system id
        # (not a hardcoded System.Embedded.1) and degrade gracefully off Dell,
        # where standard RAID is driven via the Storage/Volumes commands instead.
        system_id = self.idrac_manage_servers.rsplit("/", 1)[-1]
        r = f"{self._default_method}{self.idrac_ip}/redfish/v1/Dell/Systems/" \
            f"{system_id}/DellRaidService"

        try:
            response = self.api_get_call(r, headers)
            self.default_error_handler(response)
            data = response.json()
        except Exception:
            return CommandResult(
                {}, None, None,
                "DellRaidService is not available on this host (Dell-specific; "
                "use volumes / volume-init for standard RAID)")
        save_if_needed(filename, data)
        actions = data['Actions']
        action_dict = {}
        for a in actions:
            raid_action = a.split(".")
            if len(a) > 0 and 'target' in actions[a]:
                action_dict[raid_action[1]] = actions[a]['target']

        return CommandResult(data, action_dict, None, None)
