"""Query task services.

Command provides  query tasks service and obtains list of task.

Example::

    redfish_ctl tasks

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class TasksList(IDracManager,
                scm_type=ApiRequestType.TasksList,
                name='chassis_service_query',
                metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the tasks command."""
        super(TasksList, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the tasks subcommand and its arguments.

        :return: tuple of (ArgumentParser, command name, command help).
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
        """List tasks from the Redfish TaskService and discover their actions.

        :param filename: if set, save the response to this file.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: issue an expanded ($expand) Redfish query.
        :return: CommandResult wrapping the tasks list and per-member discovered actions.
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
