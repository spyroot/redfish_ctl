"""Query compute system settings.

    redfish_ctl compute-query

It represents  ComputerSystem schema or system instance and
the software-visible resources, or items within the data plane,
such as memory, CPU, and other devices that it can access.

6.10 added option /Settings

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class QueryCompute(IDracManager,
                   scm_type=ApiRequestType.ComputeQuery,
                   name='query',
                   metaclass=Singleton):
    """Query compute
    """
    def __init__(self, *args, **kwargs):
        """Initialize the compute-query command."""
        super(QueryCompute, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the compute-query subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
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
        """Query the host ComputerSystem settings resource.

        On firmware 6.10+ this targets the ComputerSystem ``/Settings`` URI;
        on older firmware it queries the ComputerSystem resource itself.

        :param do_expanded: will use expand output at level 1
        :param do_async: will issue asyncio request and won't block
        :param filename: if set, save the response to this file.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :return: CommandResult with the ComputerSystem settings query response.
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
