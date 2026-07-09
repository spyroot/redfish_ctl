"""iDRAC update compute settings

TODO , this looks like overlap between 6.00.3 and 6.10.

It represents  ComputerSystem schema or system instance and
the software-visible resources, or items within the data plane,
 such as memory, CPU, and other devices that it can access.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult

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


class UpdateCompute(IDracManager,
                    scm_type=ApiRequestType.ComputeUpdate,
                    name='update',
                    metaclass=Singleton):
    """
    Update idrac compute
    """

    def __init__(self, *args, **kwargs):
        super(UpdateCompute, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        help_text = "command query compute settings."
        return cmd_parser, "compute-query", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """
        :param do_expanded:
        :param do_async: will issue asyncio request and won't block
        :param filename:
        :param data_type:
        :param verbose:
        :param kwargs:
        :return:
        """

        idrac_version = self.idrac_manager_version
        ver_by_parts = idrac_version.split(".")
        major = int(ver_by_parts[0])
        minor = int(ver_by_parts[1])

        if (major, minor) >= (6, 10):
            # Support for new ComputerSystem Settings URI
            # URI: /redfish/v1/Systems/<ComputerSystem-Id>/Settings
            target_api = f"{self.idrac_manage_servers}/Settings"
        else:
            target_api = f"{self.idrac_manage_servers}"

        return self.base_query(target_api,
                               filename=filename,
                               do_async=do_async,
                               do_expanded=do_expanded)
