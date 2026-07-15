"""Query the volumes of a storage controller.

Fetches the Volumes collection for a storage controller and returns the
discovered Redfish actions along with the volume data.

    redfish_ctl volume-get --dev_id AHCI.Integrated.1-1

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class VolumeQuery(
    RedfishManagerBase, scm_type=ApiRequestType.VolumeQuery,
    name='vol_query',
    metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the volume-get command."""
        super(VolumeQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('--dev_id',
                                required=True, dest="dev_id", type=str,
                                default=False,
                                help="storage controller Example. AHCI.Integrated.1-1")

        help_text = "command query volume from storage device."
        return cmd_parser, "volume-get", help_text

    def execute(self,
                dev_id: str,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes volume get action.

        :param dev_id:
        :param do_async: note async will subscribe to an event loop.
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = f"{self.idrac_manage_servers}/Storage/{dev_id}/Volumes"
        cmd_result = self.base_query(target_api,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=True)

        actions = self.discover_member_redfish_actions(self, cmd_result.data)
        return CommandResult(cmd_result.data, actions, None, None)
