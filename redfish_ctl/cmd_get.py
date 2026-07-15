"""Generic Redfish resource GET command.

    redfish_ctl get /redfish/v1/Managers
    redfish_ctl get /redfish/v1/Systems --filename systems.json
"""
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
        """Initialize the get command."""
        super().__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``get`` command parser.

        :return: tuple of (ArgumentParser, command name, command help).
        """
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
        """Read the caller-provided Redfish resource path.

        :param uri: Redfish resource URI to read (e.g. ``/redfish/v1/Managers``).
        :param filename: if set, save the response to this file.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: issue an expanded ($expand) Redfish query.
        :return: CommandResult with the fetched resource data.
        """
        return self.base_query(
            uri,
            filename=filename,
            do_async=do_async,
            do_expanded=do_expanded,
        )
