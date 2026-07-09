"""iDRAC query privilege registry

Command query privilege registry.
Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class QueryPrivilegeRegistry(IDracManager,
                             scm_type=ApiRequestType.PrivilegeRegistry,
                             name='query_privilege_registry',
                             metaclass=Singleton):
    """A command query iDRAC resource based on a resource path.
    """
    def __init__(self, *args, **kwargs):
        super(QueryPrivilegeRegistry, self).__init__(*args, **kwargs)
        # maps from cli choice to a key in respond

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        help_text = "command query privilege registry service."
        return cmd_parser, "privilege-registry", help_text

    def execute(self,
                schema_filter: Optional[str] = None,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes query privilege registry

        :param schema_filter: filter account services based on schema filter key.
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        cmd_result = self.base_query(f"{self.idrac_members}/PrivilegeRegistry",
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=do_expanded)
        return cmd_result
