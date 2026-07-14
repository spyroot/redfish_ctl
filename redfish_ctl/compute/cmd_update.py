"""iDRAC update compute settings

TODO , this looks like overlap between 6.00.3 and 6.10.

It represents  ComputerSystem schema or system instance and
the software-visible resources, or items within the data plane,
 such as memory, CPU, and other devices that it can access.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class UpdateCompute(RedfishManagerBase,
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
        help_text = "command update compute settings."
        # Distinct from QueryCompute's "compute-query": a duplicate subcommand
        # name silently clobbers dispatch on Python 3.10 and raises
        # argparse.ArgumentError when the parser is built on 3.11+.
        return cmd_parser, "compute-update", help_text

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
