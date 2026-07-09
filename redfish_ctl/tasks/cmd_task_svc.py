"""iDRAC manager command

Command provides the option to retrieve task services.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class Manager(IDracManager,
              scm_type=ApiRequestType.ManagerQuery,
              name='task_svc_query',
              metaclass=Singleton):
    """iDRAC Manager server Command, fetch manager service,
    caller can save to a file or output to a file or pass downstream.
    """
    def __init__(self, *args, **kwargs):
        super(Manager, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args
        :param cls:
        :return:
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
        """Queries task servers services from iDRAC.
        :param do_async:
        :param verbose:
        :param do_deep:
        :param filename: if filename indicate call will save a bios setting to a file.
        :param data_type:
        :return:
        :raise: AuthenticationFailed, UnexpectedResponse
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
