"""iDRAC Redfish API with Dell OEM extension
to boot from network ISO.

python idrac_ctl.py oem-boot-netios --ip_addr $MYIP \
--share_name sambashare --remote_image ubuntu-22.04.1-desktop-amd64.iso

python idrac_ctl.py oem-attach-status
{
    "DriversAttachStatus": "NotAttached",
    "ISOAttachStatus": "Attached"
}

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional
from ..cmd_exceptions import InvalidJsonSpec
from ..cmd_utils import from_json_spec
from ..idrac_shared import IdracApiRespond
from ..redfish_shared import RedfishJson
from ..cmd_utils import str2bool
from ..idrac_shared import IdracApiRespond, ResetType
from ..cmd_utils import save_if_needed
from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond, Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..idrac_shared import IDRAC_API
from ..idrac_shared import IdracApiRespond


class DellOemNetIsoBoot(IDracManager,
                        scm_type=ApiRequestType.DellOemNetIsoBoot,
                        name='delloem_netios_boot',
                        metaclass=Singleton):
    """A command uses dell oem to attach ISO
    """

    def __init__(self, *args, **kwargs):
        super(DellOemNetIsoBoot, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_remote_share=True)
        help_text = "command boot from network iso "
        return cmd_parser, "oem-boot-netios", help_text

    def execute(self,
                ip_addr: Optional[str] = None,
                share_type: Optional[str] = None,
                share_name: Optional[str] = None,
                remote_image: Optional[str] = None,
                remote_username: Optional[str] = None,
                remote_password: Optional[str] = None,
                remote_workgroup: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes dell oem ConnectNetworkISOImage

        :param ip_addr: ip address of NFS or CIFS
        :param share_type: NFS|CIFS
        :param share_name: share name
        :param remote_image:  path to image
        :param remote_username: remote username if required for NFS or CIFS
        :param remote_password: remote password if required for NFS or CIFS
        :param remote_workgroup:
        :param do_async: note async will subscribe to an event loop.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        cmd_result = self.sync_invoke(ApiRequestType.DellOemActions, "dell_oem_actions")
        redfish_action = cmd_result.discovered['BootToNetworkISO']
        target_api = redfish_action.target

        payload = {
            'IPAddress': ip_addr,
            'ShareType': share_type,
            'ShareName': share_name,
            'ImageName': remote_image,
            'UserName': remote_username,
            'Password': remote_password,
            'Workgroup': remote_workgroup,
        }

        for key, value in dict(payload).items():
            if value is None:
                del payload[key]

        cmd_result, api_resp = self.base_post(
            target_api, payload=payload,
            do_async=do_async, expected_status=202)

        if api_resp == IdracApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            self.logger.info(f"Fetching task {task_id} state.")
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result
