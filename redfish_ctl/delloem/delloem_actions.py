"""iDRAC Redfish API with Dell OEM extension
to get available actions.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class DellOemActions(IDracManager,
                     scm_type=ApiRequestType.DellOemActions,
                     name='dell_oem_actions',
                     metaclass=Singleton):
    """A command query oem actions.
    """
    def __init__(self, *args, **kwargs):
        super(DellOemActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        help_text = "command get supported dell os oem actions"
        return cmd_parser, "oem-actions", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes query for dell oem actions.

        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = "/redfish/v1/Dell/Systems/System.Embedded.1/DellOSDeploymentService"
        cmd_result = self.base_query(target_api,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)

        actions = {}
        if isinstance(cmd_result.data, dict) and 'Actions' in cmd_result.data:
            action = self.discover_redfish_actions(self, cmd_result.data)
            actions.update(action)

        return CommandResult(cmd_result, actions, None, None)
