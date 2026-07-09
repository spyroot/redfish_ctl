"""iDRAC query boot source registry.

And the registry defines a representation of Boot Sources instances

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed
from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond, Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..idrac_shared import IDRAC_API


class QueryBootSourceRegistry(
    IDracManager,
    scm_type=ApiRequestType.BootSourceRegistry,
    name='boot_source_registry',
    metaclass=Singleton):
    """A command query iDRAC resource based on a resource path.
    """

    def __init__(self, *args, **kwargs):
        super(QueryBootSourceRegistry, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        help_text = "command query boot source registry."
        return cmd_parser, "boot-source-registry", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes boot source registry query command.
        
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        # BootSources/BootSourcesRegistry is a Dell OEM resource; degrade
        # gracefully off Dell (standard boot config is via the boot-options /
        # change-boot-order / boot-one-shot commands).
        try:
            return self.base_query(
                f"{self.idrac_manage_servers}{IDRAC_API.BootSourcesRegistryQuery}",
                filename=filename, do_async=do_async, do_expanded=do_expanded)
        except Exception:
            return CommandResult(
                {}, None, None,
                "BootSourcesRegistry is not available on this host (Dell-specific)")
