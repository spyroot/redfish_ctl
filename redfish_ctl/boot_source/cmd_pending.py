"""Query boot source pending values.

Command provides option to query boot source for
pending values.

Example.
redfish_ctl boot-pending

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..cmd_utils import save_if_needed, str2bool
from ..idrac_manager import IDracManager
from ..idrac_shared import REDFISH_API, ApiRequestType, RedfishApiRespond, ResetType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishJson


class BootSourcePending(IDracManager,
                        scm_type=ApiRequestType.BootSourcePending,
                        name='query_pending',
                        metaclass=Singleton):
    """A command query dell OEM for boot source pending changes.
    """
    def __init__(self, *args, **kwargs):
        """Initialize the boot-pending command."""
        super(BootSourcePending, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_reboot=False)
        cmd_parser.add_argument(
            '-r', '--filter', required=False,
            dest="data_filter", type=str, default="",
            help="filters on pending value."
        )

        help_text = "command query for boot source a current pending values"
        return cmd_parser, "boot-pending", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                data_filter: Optional[str] = None,
                **kwargs) -> CommandResult:
        """Executes and query boot source pending.

        BootSources settings,  require a system reset to apply.

        :param data_filter: filter applied to find specific device.
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: will do expand query
        :param filename: if filename indicate call will save the response to this file.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :return: CommandResult and if filename provide will save to a file.
        """
        # DellBootSources is a Dell OEM resource; degrade gracefully off Dell.
        target_api = f"{self.idrac_manage_servers}/Oem/Dell/DellBootSources/Settings"

        try:
            cmd_result = self.base_query(
                target_api, filename=filename,
                do_async=do_async, do_expanded=do_expanded
            )
        except Exception:
            return CommandResult(
                {}, None, None,
                "DellBootSources is not available on this host (Dell-specific)")
        if cmd_result.error is not None:
            return cmd_result

        if cmd_result.data is not None and RedfishJson.Attributes in cmd_result.data:
            attr_data = cmd_result.data[RedfishJson.Attributes]
            attr_cmd = CommandResult(attr_data, None, None, None)
            if data_filter is not None and len(data_filter) > 0:
                if data_filter in attr_data:
                    return CommandResult(
                        attr_data[data_filter], None, None, None
                    )
            else:
                cmd_result = attr_cmd

        return cmd_result
