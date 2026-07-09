"""iDRAC Redfish API with Dell OEM extension
to get network ISO attach status.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class GetNetworkIsoAttachStatus(IDracManager,
                                scm_type=ApiRequestType.GetNetworkIsoAttachStatus,
                                name='net_ios_attach_status',
                                metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        super(GetNetworkIsoAttachStatus, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_reboot=True)
        help_text = "command get network iso status"
        return cmd_parser, "oem-net-ios-status", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                do_reboot: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes dell oem get attach status action.

        Return if drivers attached and ISO attached.

        {
            "DriversAttachStatus": "NotAttached",
            "ISOAttachStatus": "NotAttached"
        }
        python idrac_ctl.py chassis
        :param do_reboot: reboot
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        cmd_result = self.sync_invoke(ApiRequestType.DellOemActions, "dell_oem_actions")
        redfish_action = cmd_result.discovered['GetNetworkISOImageConnectionInfo']
        target_api = redfish_action.target

        cmd_result, api_resp = self.base_post(target_api, do_async=do_async)
        result = {}
        resp_keys = [
            "HostAttachedStatus",
            "HostBootedFromISO",
            "IPAddr", "ISOConnectionStatus",
            "ImageName", "ShareName", "UserName"
        ]

        if cmd_result is not None and cmd_result.extra is not None:
            data = cmd_result.extra.json()
            for rk in resp_keys:
                if rk in data:
                    result[rk] = data[rk]

        if api_resp == IdracApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            self.logger.info(f"Fetching task {task_id} state.")
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        if do_reboot:
            self.reboot()

        return cmd_result
