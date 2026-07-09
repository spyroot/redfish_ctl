"""iDRAC query jobs services

Command query jobs services.
It  represents the properties for the job service
and has links to jobs managed by the job service.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import IDRAC_API
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class JobServices(IDracManager,
                  scm_type=ApiRequestType.JobServices,
                  name='job_service_query',
                  metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        super(JobServices, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        help_text = "command query jobs services"
        return cmd_parser, "jobs-service", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                do_capability: Optional[bool]= False,
                **kwargs) -> CommandResult:
        """Executes query job services.
        python idrac_ctl.py

        ServiceCapabilities": {
                 "MaxJobs": 256,
                 "MaxSteps": 1,
                 "Scheduling": true
             }

        # return is capability , specifically  we can check if server capable to schedule jobs

        :param do_capability:  return service ServiceCapabilities
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        cmd_result = self.base_query(IDRAC_API.JobServiceQuery,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)

        if do_capability is True and 'ServiceCapabilities' in cmd_result.data:
            CommandResult(cmd_result.data['ServiceCapabilities'], None, None, None)

        return CommandResult(cmd_result, None, None, None)
