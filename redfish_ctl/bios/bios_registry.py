"""BIOS attribute registry query command.

Command provides raw query bios registry.
redfish_ctl bios-registry --attr_name SystemServiceTag,OldSetupPassword

Will return SystemServiceTag,OldSetupPassword and list of all attributes.

If we only need get values for particular options.
redfish_ctl --nocolor -d bios-registry --attr_name EnergyPerformanceBias --option_only

redfish_ctl bios-registry --attr_list --attr_name SystemServiceTag,OldSetupPassword

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from collections.abc import Iterator
from http import HTTPStatus
from typing import Optional
from urllib.parse import quote

from ..cmd_exceptions import ResourceNotFound
from ..cmd_utils import save_if_needed
from ..redfish_api_common import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult, RedfishManager
from ..redfish_shared import RedfishApi, RedfishJson


def _query_bios_resource(
        manager: RedfishManager, uri: str, do_async: Optional[bool], *,
        do_expanded: Optional[bool] = False,
        filename: Optional[str] = None) -> CommandResult:
    """Read an optional BIOS resource and preserve all errors except HTTP 404.

    :param manager: the manager bound to the caller's controller.
    :param uri: the BIOS resource path.
    :param do_async: whether to use the asynchronous GET transport.
    :param do_expanded: whether to expand collection links.
    :param filename: optional destination for the raw resource response.
    :return: the query result, with absent data on HTTP 404.
    :raises ResourceNotFound: when the query reports a different failure status.
    """
    try:
        return manager.base_query(
            uri, do_async=do_async, do_expanded=do_expanded, filename=filename,
        )
    except ResourceNotFound as exc:
        if not exc.args or getattr(exc.args[0], "status_code", None) != HTTPStatus.NOT_FOUND:
            raise
        return CommandResult(None, None, None, None)


def _standard_bios_registry_uris(
        manager: RedfishManager, bios_uri: str, do_async: Optional[bool]) -> Iterator[str]:
    """Find the local registry file locations named by the BIOS resource.

    :param manager: the manager bound to the caller's controller.
    :param bios_uri: the BIOS resource path.
    :param do_async: whether to use the asynchronous GET transport.
    :return: an iterator of the validated registry file paths in advertised order.
    """
    bios = _query_bios_resource(manager, bios_uri, do_async).data
    name = bios.get("AttributeRegistry") if isinstance(bios, dict) else None
    if not isinstance(name, str) or not name or "/" in name:
        return

    uri = manager._local_resource_uri(f"{RedfishApi.Version}/Registries/{quote(name, safe='')}")
    if not uri:
        return
    metadata = _query_bios_resource(manager, uri, do_async).data
    locations = metadata.get(RedfishJson.Location) if isinstance(metadata, dict) else None
    if not isinstance(locations, list):
        return
    for location in locations:
        if isinstance(location, dict):
            registry_uri = manager._local_resource_uri(location.get("Uri"))
            if registry_uri:
                yield registry_uri


def _query_bios_registry(
        manager: RedfishManager, do_async: Optional[bool], *,
        do_expanded: Optional[bool] = False,
        filename: Optional[str] = None) -> CommandResult:
    """Read the attribute registry named by BIOS for inspection or staging.

    :param manager: the manager bound to the caller's controller.
    :param do_async: whether to use the asynchronous GET transport.
    :param do_expanded: whether to expand links in the registry response.
    :param filename: optional destination for the raw registry response.
    :return: the registry response or an empty result if unavailable.
    """
    system_uri = manager.managed_system_uri
    if not system_uri:
        return CommandResult(None, None, None, None)

    bios_uri = f"{system_uri}{RedfishApi.Bios}"
    for registry_uri in _standard_bios_registry_uris(manager, bios_uri, do_async):
        result = _query_bios_resource(
            manager, registry_uri, do_async,
            do_expanded=do_expanded, filename=filename,
        )
        if result.error is not None or result.data is not None:
            return result

    if manager.uses_dell_job_semantics:
        return _query_bios_resource(
            manager, f"{system_uri}{REDFISH_API.BiosRegistry}", do_async,
            do_expanded=do_expanded, filename=filename,
        )
    return CommandResult(None, None, None, None)


class BiosRegistry(RedfishManager,
                   scm_type=ApiRequestType.BiosRegistry,
                   name='bios_registry',
                   metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        """Construct the bios-registry command, forwarding credentials to the base manager."""
        super(BiosRegistry, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--registry_only',
            action='store_true',
            required=False, dest="is_registry_only",
            default=True,
            help="return only registry")

        cmd_parser.add_argument(
            '--attr_only',
            action='store_true',
            required=False, dest="is_attr_only",
            default=False,
            help="return only attribute")

        cmd_parser.add_argument(
            '--attr_name', type=str,
            required=False, dest="attr_name",
            default=None,
            help="attribute name or list. "
                 "(Example --attr_name SystemServiceTag,OldSetupPassword)")

        cmd_parser.add_argument(
            '--attr_list', action='store_true',
            required=False, dest="attr_list",
            default=False,
            help="return list of all attribute names.")

        cmd_parser.add_argument(
            '--option_only', action='store_true',
            required=False, dest="is_value_only",
            default=False,
            help="will only output for given attribute value choices. Example: "
                 "(bios-registry --attr_name EnergyPerformanceBias --option_only)"
        )

        cmd_parser.add_argument(
            '--noreadonly', action='store_true',
            required=False, dest="no_read_only", default=False,
            help="will filter out from output all read-only attributes.")

        help_text = "command query bios registry attributes"
        return cmd_parser, "bios-registry", help_text

    def execute(self,
                is_registry_only: Optional[bool] = False,
                is_attr_only: Optional[bool] = False,
                attr_name: Optional[str] = None,
                attr_list: Optional[bool] = False,
                no_read_only: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                is_value_only: Optional[bool] = True,
                **kwargs) -> CommandResult:
        """Executes query bios registry.

        :param no_read_only:  filters, remove all read only.
        :param attr_list: will also return list of all attributes
        :param attr_name: will filler based on attribute names.
        :param is_registry_only: will return complete registry. (Default)
        :param is_attr_only: will return all attributes
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param is_value_only:  will only return value , choices that attribute can take.
        :param filename: if filename indicate call will save the response to this file.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :return: CommandResult and if filename provide will save to a file.
        """
        cmd_result = _query_bios_registry(self, do_async, do_expanded=do_expanded)
        if cmd_result.error is not None:
            return cmd_result
        filtered_result = []
        attribute_names = None

        # Tolerate a resource that has no registry entries (e.g. registry not
        # reachable on this host) instead of raising a KeyError.
        registry = cmd_result.data.get(RedfishJson.RegistryEntries) \
            if isinstance(cmd_result.data, dict) else None
        if not isinstance(registry, dict) or RedfishJson.Attributes not in registry:
            return CommandResult([], None, None, None)
        data = registry[RedfishJson.Attributes]

        if attr_list and isinstance(data, list):
            attribute_names = [d['AttributeName'] for d in data]

        if attr_name is not None and len(attr_name) > 0:
            attr_names = attr_name.split(",") if "," in attr_name else [attr_name]

            # filter by attribute name
            if isinstance(data, list):
                for n in attr_names:
                    for entry in data:
                        if entry['AttributeName'] == n:
                            filtered_result.append(entry)
                            break
                data = filtered_result
            elif isinstance(data, dict):
                data = data[attr_name]
        else:
            if is_registry_only:
                data = cmd_result.data['RegistryEntries']
            if is_registry_only:
                registry = cmd_result.data['RegistryEntries']
                data = registry['Attributes']

        filtered_read_only = []
        # filter out all
        if no_read_only:
            if 'RegistryEntries' in data:
                source_data = registry['RegistryEntries']['Attributes']
            elif 'Attributes' in data:
                source_data = registry['Attributes']
            elif isinstance(data, list):
                source_data = data
            else:
                raise ValueError("unknown value.")

            for entry in source_data:
                if entry['ReadOnly']:
                    continue
                else:
                    filtered_read_only.append(entry)
            data = filtered_read_only

        save_if_needed(filename, data)

        # filter only value choices
        if is_value_only:
            data = [entry['Value'] for
                    entry in data
                    if isinstance(entry, dict) and 'Value' in entry][0]

        return CommandResult(data, None, attribute_names, None)
