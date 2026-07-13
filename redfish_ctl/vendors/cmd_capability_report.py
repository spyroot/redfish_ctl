"""Export registered vendor capability profiles."""

from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..cmd_utils import save_if_needed
from ..command_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from .report import capability_report


class CapabilityReport(CommandBase,
                       scm_type=ApiRequestType.CapabilityReport,
                       name="capability-report",
                       metaclass=Singleton):
    """Emit local vendor capability profiles as machine-readable data."""

    def __init__(self, *args, **kwargs):
        super(CapabilityReport, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the local capability-report subcommand."""
        cmd_parser = cls.base_parser(is_async=False, is_expanded=False)
        cmd_parser.add_argument(
            "--vendor",
            required=False,
            default=None,
            help="limit the report to one vendor profile",
        )
        return (
            cmd_parser,
            "capability-report",
            "command export vendor capability profiles for IaC consumers",
        )

    def execute(self,
                vendor: Optional[str] = None,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Return registered vendor capability profiles without BMC access."""
        data = capability_report(vendor)
        save_if_needed(filename, data, data_format=data_type)
        return CommandResult(data, None, None, None)
