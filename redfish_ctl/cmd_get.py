"""Generic Redfish resource GET command.

    redfish_ctl get /redfish/v1/Managers
    redfish_ctl get /redfish/v1/Systems --filename systems.json
"""
from abc import abstractmethod
from typing import Optional
from urllib.parse import unquote, urlsplit, urlunsplit

from .cmd_exceptions import InvalidArgument
from .redfish_manager_base import RedfishManagerBase
from .redfish_manager_shared import ApiRequestType, Singleton
from .redfish_manager import CommandResult


def _decode_path_for_validation(path: str) -> str:
    """Decode path escapes enough to catch encoded traversal controls."""
    decoded = path
    for _ in range(3):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return decoded
        decoded = next_decoded
    return decoded


def _normalize_redfish_uri(uri: str) -> str:
    """Validate and normalize a caller-provided Redfish resource URI.

    :param uri: resource URI supplied to the ``get`` command.
    :return: normalized path, with a query string preserved when present.
    :raises InvalidArgument: when the value is not a local ``/redfish/v1`` path.
    """
    if not isinstance(uri, str) or not uri.strip():
        raise InvalidArgument("get requires a /redfish/v1 resource URI")

    raw_uri = uri.strip()
    if "\\" in raw_uri:
        raise InvalidArgument("get URI must use forward slashes")

    parsed = urlsplit(raw_uri)
    if parsed.scheme or parsed.netloc:
        raise InvalidArgument("get accepts Redfish resource paths, not absolute URLs")
    if parsed.fragment:
        raise InvalidArgument("get URI must not include a fragment")

    path = parsed.path
    if path != "/redfish/v1" and not path.startswith("/redfish/v1/"):
        raise InvalidArgument("get URI must start with /redfish/v1")

    decoded_path = _decode_path_for_validation(path)
    if "\\" in decoded_path:
        raise InvalidArgument("get URI must use forward slashes")

    for segment in decoded_path.split("/"):
        if segment in {".", ".."}:
            raise InvalidArgument("get URI must not contain path traversal segments")

    return urlunsplit(("", "", path, parsed.query, ""))


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
        resource_uri = _normalize_redfish_uri(uri)
        return self.base_query(
            resource_uri,
            filename=filename,
            do_async=do_async,
            do_expanded=do_expanded,
        )
