"""iDRAC query jobs services

The DellJobService resource provides some actions to support
Job management functionality.  Query and discovery.

Example::

    redfish_ctl jobs-dell-service

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class JobDellServices(RedfishManagerBase,
                      scm_type=ApiRequestType.JobDellServices,
                      name='job_service_query',
                      metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the jobs-dell-service command."""
        super(JobDellServices, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_expanded=True)
        help_text = "command query jobs services"
        return cmd_parser, "jobs-dell-service", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes query job services.
        python redfish_ctl.py query
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a response to a file.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService"
        cmd_result = self.base_query(target_api,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)

        actions = self.discover_redfish_actions(self, cmd_result.data)
        return CommandResult(cmd_result, None, actions, None)
