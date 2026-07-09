"""iDRAC Redfish API with Dell OEM extension to
disconnect network iso.

python idrac_ctl.py oem-disconnect

Author Mus spyroot@gmail.com
"""

from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class DellOemDisconnect(IDracManager,
                        scm_type=ApiRequestType.DellOemDisconnect,
                        name='delloem_disconnect',
                        metaclass=Singleton):
    """A command uses dell oem to disconnect ISO
    """

    def __init__(self, *args, **kwargs):
        super(DellOemDisconnect, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_remote_share=False)
        help_text = "command disconnect network iso"
        return cmd_parser, "oem-disconnect", help_text

    def execute(self,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes dell oem disconnect, detach network iso.

        :param do_async:note async will subscribe to an event loop.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        cmd_result = self.sync_invoke(ApiRequestType.DellOemActions, "dell_oem_actions")
        redfish_action = cmd_result.discovered['DisconnectNetworkISOImage']
        target_api = redfish_action.target

        cmd_result, api_resp = self.base_post(
            target_api, do_async=do_async, expected_status=200
        )

        if api_resp == IdracApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            self.logger.info(f"Fetching task {task_id} state.")
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result
