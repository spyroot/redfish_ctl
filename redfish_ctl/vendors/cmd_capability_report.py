"""Export registered vendor capability profiles.

Emits the local vendor capability profiles as machine-readable data; needs no
BMC access.

    redfish_ctl capability-report --vendor dell
"""

from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from .report import capability_report


class CapabilityReport(RedfishManagerBase,
                       scm_type=ApiRequestType.CapabilityReport,
                       name="capability-report",
                       metaclass=Singleton):
    """Emit local vendor capability profiles as machine-readable data."""

    def __init__(self, *args, **kwargs):
        """Initialize the capability-report command."""
        super(CapabilityReport, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the local capability-report subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
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
        """Return registered vendor capability profiles without BMC access.

        :param vendor: if set, limit the report to this one vendor profile;
            when ``None``, include every registered vendor.
        :param filename: if set, save the response to this file.
        :param data_type: data format used when saving; json, yaml, xml, etc.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: accepted for CLI compatibility; not used by this command.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: a :class:`CommandResult` whose data is the capability report dict.
        """
        data = capability_report(vendor)
        save_if_needed(filename, data, data_format=data_type)
        return CommandResult(data, None, None, None)
