"""Query boot options.

    redfish_ctl boot-options

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class BootOptionsQuery(IDracManager,
                       scm_type=ApiRequestType.BootOptionQuery,
                       name='boot_options_query',
                       metaclass=Singleton):
    """
    Command enable boot option
    """

    def __init__(self, *args, **kwargs):
        """Initialize the boot-options command."""
        super(BootOptionsQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        help_text = "command fetch the boot options"
        return cmd_parser, "boot-options", help_text

    def execute(self,
                filename: Optional[str] = None,
                do_async: Optional[bool] = False,
                data_type: Optional[str] = "json",
                do_expanded: Optional[str] = True,
                verbose: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Query information for boot options.

        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
         :param filename: if filename indicate call will save the response to this file.
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = f"{self.idrac_manage_servers}/BootOptions"
        return self.base_query(target_api,
                               filename=filename,
                               do_async=do_async,
                               do_expanded=True)
