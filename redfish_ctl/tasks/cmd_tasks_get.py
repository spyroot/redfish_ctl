"""Query task services.

Command provides query tasks service and obtains list of task.

Example::

    redfish_ctl task-get

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidJsonSpec
from ..cmd_utils import from_json_spec
from ..redfish_manager_shared import RedfishApiRespond
from ..redfish_shared import RedfishJson
from ..cmd_utils import str2bool
from ..redfish_manager_shared import RedfishApiRespond, ResetType
from ..cmd_utils import save_if_needed
from ..cmd_exceptions import InvalidArgument
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import RedfishApiRespond, Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..redfish_manager_shared import REDFISH_API
from ..redfish_manager_shared import RedfishApiRespond

class TasksGet(RedfishManagerBase,
               scm_type=ApiRequestType.TaskGet,
               name='chassis_service_query',
               metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the task-get command."""
        super(TasksGet, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the task-get subcommand and its arguments.

        :return: tuple of (ArgumentParser, command name, command help).
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
        """Fetch a single task by its id from the Redfish TaskService.

        :param task_id: task id to fetch (appended to the Tasks resource path).
        :param filename: if set, save the response to this file.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: issue an expanded ($expand) Redfish query.
        :return: CommandResult wrapping the task query result.
        """
        target_api = f"/redfish/v1/TaskService/Tasks/{task_id}"
        cmd_result = self.base_query(target_api,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)
        return CommandResult(cmd_result, None, None, None)
