"""Generic Redfish resource GET command."""
from abc import abstractmethod
from typing import Optional

from .redfish_manager_base import RedfishManagerBase
from .redfish_manager_shared import ApiRequestType, Singleton
from .redfish_manager import CommandResult


class RawGet(
    RedfishManagerBase,
    scm_type=ApiRequestType.RawGet,
    name="raw_get",
    metaclass=Singleton,
):
    """Read an arbitrary Redfish resource path."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``get`` command parser."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "uri",
            type=str,
            help="Redfish resource URI, for example /redfish/v1/Managers",
        )
        help_text = "read an arbitrary Redfish resource URI."
        return cmd_parser, "get", help_text

    def execute(
            self,
            uri: str,
            filename: Optional[str] = None,
            data_type: Optional[str] = "json",
            verbose: Optional[bool] = False,
            do_async: Optional[bool] = False,
            do_expanded: Optional[bool] = False,
            **kwargs,
    ) -> CommandResult:
        """Read the caller-provided Redfish resource path."""
        return self.base_query(
            uri,
            filename=filename,
            do_async=do_async,
            do_expanded=do_expanded,
        )
