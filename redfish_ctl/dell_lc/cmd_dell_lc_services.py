"""iDRAC query Dell LC services

Command provides query and provide action LC services
supports.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class DellLcQuery(IDracManager,
                  scm_type=ApiRequestType.DellLcQuery,
                  name='dell_lc_services',
                  metaclass=Singleton):
    """A command query Dell LC services.
    """
    def __init__(self, *args, **kwargs):
        super(DellLcQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('--filter',
                                required=False, dest="data_filter", type=str,
                                default=False, help="filter on key. Example PowerState")

        help_text = "command query dell-lc services"
        return cmd_parser, "dell-lc-svc", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                data_filter: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes query for dell LC.
        python idrac_ctl.py chassis
        :param data_filter:
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService"
        if data_filter:
            do_expanded = True

        cmd_result = self.base_query(target_api,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)
        actions = self.discover_redfish_actions(self, cmd_result.data)
        return CommandResult(cmd_result, actions, None, None)
