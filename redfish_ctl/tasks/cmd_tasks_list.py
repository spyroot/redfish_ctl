"""iDRAC query tasks services

Command provides  query tasks service and obtains list of task.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class TasksList(IDracManager,
                scm_type=ApiRequestType.TasksList,
                name='chassis_service_query',
                metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        super(TasksList, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        help_text = "command fetch tasks list"
        return cmd_parser, "tasks", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = True,
                **kwargs) -> CommandResult:
        """Executes tasks list

        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = "/redfish/v1/TaskService/Tasks"
        cmd_result = self.base_query(target_api,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)

        actions = {}
        if 'Members' in cmd_result.data:
            member_data = cmd_result.data['Members']
            for m in member_data:
                if isinstance(m, dict):
                    if 'Actions' in m.keys():
                        action = self.discover_redfish_actions(self, m)
                        actions.update(action)

        return CommandResult(cmd_result, actions, None, None)
