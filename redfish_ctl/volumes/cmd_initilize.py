"""Initialize a volume on a storage controller.

Discovers the volume's ``Initialize`` Redfish action and posts a fast
initialize request to the storage controller.

    redfish_ctl volume-init --dev_id AHCI.Integrated.1-1 --vol_id Disk.Direct.0-0:AHCI.Integrated.1-1

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import UnsupportedAction
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class VolumeInit(IDracManager,
                 scm_type=ApiRequestType.VolumeInit,
                 name='chassis_service_query',
                 metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the volume-init command."""
        super(VolumeInit, self).__init__(*args, **kwargs)

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
                                default=False, help="storage controller (Example: AHCI.Integrated.1-1)")
        cmd_parser.add_argument('--vol_id',
                                required=True, dest="vol_id", type=str,
                                default=False, help="vol disk (Example: "
                                                    "Disk.Direct.0-0:AHCI.Integrated.1-1)")

        help_text = "command initialize volume.."
        return cmd_parser, "volume-init", help_text

    def execute(self,
                dev_id: str,
                vol_id: str,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                data_filter: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes query for chassis.
        python redfish_ctl.py chassis
        :param vol_id:
        :param dev_id:
        :param data_filter:
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :return: CommandResult and if filename provide will save to a file.
        """
        vol_data = self.sync_invoke(ApiRequestType.VolumeQuery,
                                    "vol_query", dev_id=dev_id)
        if 'Initialize' not in vol_data.discovered:
            raise UnsupportedAction(f"Device {dev_id} "
                                    f"doesn't support this action. "
                                    f"Supported {vol_data.discovered.keys()}")

        redfish_action = vol_data.discovered['Initialize']
        target_api = redfish_action.target
        payload = {'InitializeType': "Fast"}
        cmd_result, _ = self.base_post(target_api, payload, do_async=do_async)
        return CommandResult(cmd_result.data, None, None, None)
