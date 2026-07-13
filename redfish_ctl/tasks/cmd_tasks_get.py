"""iDRAC query tasks services

Command provides query tasks service and obtains list of task.


Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..command_shared import (
    ApiRequestType,
    Singleton,
)
from ..redfish_manager import CommandResult


class TasksGet(CommandBase,
               scm_type=ApiRequestType.TaskGet,
               name='chassis_service_query',
               metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        super(TasksGet, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('-t' '--task_id', required=True, dest="task_id", type=str,
                                default=None, help="Job id. Example JID_744718373591")

        help_text = "command fetch current task"
        return cmd_parser, "task-get", help_text

    def execute(self,
                task_id: str,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = True,
                **kwargs) -> CommandResult:
        """Executes tasks fetch

        :param task_id:
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = f"/redfish/v1/TaskService/Tasks/{task_id}"
        cmd_result = self.base_query(target_api,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)
        return CommandResult(cmd_result, None, None, None)
