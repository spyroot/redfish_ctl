"""Manager view command.

Command provides the option to retrieve the Redfish manager view.
redfish_ctl manager

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class Manager(IDracManager,
              scm_type=ApiRequestType.ManagerQuery,
              name='manager_query',
              metaclass=Singleton):
    """Manager view command, fetch the manager service,
    caller can save to a file or output to a file or pass downstream.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the manager command."""
        super(Manager, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args
        :param cls:
        :return:
        """
        cmd_arg = cls.base_parser()
        help_text = "command fetch the manager view"
        return cmd_arg, "manager", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                do_deep: Optional[bool] = False,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = True,
                **kwargs) -> CommandResult:
        """Queries manager services from a Redfish endpoint.
        :param do_expanded:
        :param do_async:
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_deep:
        :param filename: if filename indicate call will save the response to this file.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :return:
        :raise: AuthenticationFailed, UnexpectedResponse
        """
        cmd_result = self.base_query(self.idrac_members,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)

        redfish_actions = self.discover_redfish_actions(self, cmd_result.data)
        return CommandResult(cmd_result.data, redfish_actions, None, None)
