"""Task service command.

Command provides the option to retrieve task services.

Example::

    redfish_ctl task-svc

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class Manager(RedfishManagerBase,
              scm_type=ApiRequestType.ManagerQuery,
              name='task_svc_query',
              metaclass=Singleton):
    """Task service command, fetch the task service,
    caller can save to a file or output to a file or pass downstream.
    """
    def __init__(self, *args, **kwargs):
        """Initialize the task-svc command."""
        super(Manager, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the task-svc subcommand and its arguments.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_arg = cls.base_parser()
        help_text = "command fetch task services"
        return cmd_arg, "task-svc", help_text

    def execute(self, filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                do_deep: Optional[bool] = False,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Query the Redfish TaskService and discover its actions.

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: json or xml; json adds the JSON content-type header.
        :param do_deep: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: accepted for CLI compatibility; not used by this command.
        :return: CommandResult wrapping the TaskService payload and discovered actions.
        :raises AuthenticationFailed, UnexpectedResponse: on request failure.
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)
        target = "/redfish/v1/TaskService"
        r = f"{self._default_method}{self.idrac_ip}{target}"
        response = self.api_get_call(r, headers)
        data = response.json()
        redfish_actions = self.discover_redfish_actions(self, data)
        return CommandResult(data, redfish_actions, None, None)
