"""iDRAC Redfish API with Dell OEM extension
to get network ISO attach status.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class GetAttachStatus(
    IDracManager,
    scm_type=ApiRequestType.GetAttachStatus,
    name='get_attach_status',
    metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        super(GetAttachStatus, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        help_text = "command get attach status "
        return cmd_parser, "oem-attach-status", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes dell oem get attach status action.

        Return if drivers attached and ISO attached.

        {
            "DriversAttachStatus": "NotAttached",
            "ISOAttachStatus": "NotAttached"
        }
        python idrac_ctl.py chassis
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a response to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        cmd_result = self.sync_invoke(
            ApiRequestType.DellOemActions,
            "dell_oem_actions"
        )

        redfish_action = cmd_result.discovered['GetAttachStatus']
        target_api = redfish_action.target
        cmd_result, api_resp = self.base_post(target_api, do_async=do_async)
        print(f"target {cmd_result}")

        if cmd_result.error is not None:
            return cmd_result

        result = {}
        if cmd_result is not None and cmd_result.extra is not None:
            data = cmd_result.extra.json()
            if 'DriversAttachStatus' in data:
                result['DriversAttachStatus'] = data['DriversAttachStatus']
            if 'ISOAttachStatus' in data:
                result['ISOAttachStatus'] = data['ISOAttachStatus']

        return CommandResult(result, None, None, None)
