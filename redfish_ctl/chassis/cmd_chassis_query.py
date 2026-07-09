"""iDRAC query chassis services

Command provides raw query chassis
and provide list of supported actions.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional
from ..idrac_shared import IDRAC_API
from ..redfish_manager import CommandResult
from ..cmd_exceptions import FailedDiscoverAction
from ..cmd_exceptions import InvalidArgument
from ..cmd_exceptions import UnsupportedAction
from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond, Singleton, ApiRequestType
from ..idrac_shared import IDRAC_JSON


class ChassisQuery(IDracManager,
                   scm_type=ApiRequestType.ChassisQuery,
                   name='chassis_service_query',
                   metaclass=Singleton):
    """A command query chassis.
    """

    def __init__(self, *args, **kwargs):
        super(ChassisQuery, self).__init__(*args, **kwargs)

    @property
    def help(self):
        return '''The Chassis schema represents the physical components of a system'''

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--filter', required=False, dest="data_filter", type=str,
            default=False, help="filter on key. Example PowerState")

        # The Chassis schema represents the physical components of a system
        help_text = "command query chassis services"
        return cmd_parser, "chassis", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                data_filter: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes query for chassis.
        python idrac_ctl.py chassis
        :param data_filter: a filter on set of keys
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        # for a filter we query on level deep
        if data_filter:
            do_expanded = True

        cmd_result = self.base_query(IDRAC_API.Chassis,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)

        actions = {}
        filter_result = {}
        if 'Members' in cmd_result.data:
            member_data = cmd_result.data['Members']
            for m in member_data:
                if isinstance(m, dict):
                    if 'Actions' in m.keys():
                        action = self.discover_redfish_actions(self, m)
                        actions.update(action)
            if data_filter:
                for m in member_data:
                    if data_filter in m:
                        filter_result[data_filter] = m[data_filter]
                        break
                cmd_result = filter_result

        return CommandResult(cmd_result, actions, None, None)
