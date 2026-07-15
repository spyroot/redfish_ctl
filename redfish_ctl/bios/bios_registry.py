"""iDRAC query bios registry

Command provides raw query bios registry.
redfish_ctl bios-registry --attr_name SystemServiceTag,OldSetupPassword

Will return SystemServiceTag,OldSetupPassword and list of all attributes.

If we only need get values for particular options.
redfish_ctl --nocolor -d bios-registry --attr_name EnergyPerformanceBias --option_only

redfish_ctl bios-registry --attr_list --attr_name SystemServiceTag,OldSetupPassword

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import save_if_needed
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishJson
from ..redfish_shared import RedfishApi


class BiosRegistry(RedfishManagerBase,
                   scm_type=ApiRequestType.BiosRegistry,
                   name='bios_registry',
                   metaclass=Singleton):
    """A command query job_service_query.
    """

    def __init__(self, *args, **kwargs):
        super(BiosRegistry, self).__init__(*args, **kwargs)

    def _resolve_registry_uri(self, do_async):
        """Locate the BIOS attribute registry URI vendor-neutrally.

        Dell exposes it directly at ``{system}/Bios/BiosRegistry``. The standard
        Redfish way (iLO and others): ``Bios.AttributeRegistry`` names a registry
        resolved under ``/redfish/v1/Registries/<name>``, whose ``Location[].Uri``
        points at the registry JSON. Prefer the Dell subpath when it resolves,
        otherwise follow the standard AttributeRegistry -> Registries -> Uri chain.
        Falls back to the Dell path so existing behavior is unchanged.
        """
        dell_uri = f"{self.idrac_manage_servers}/Bios/BiosRegistry"
        try:
            if (self.base_query(dell_uri, do_async=do_async).data or {}).get("RegistryEntries"):
                return dell_uri
        except Exception:
            pass
        try:
            bios = self.base_query(f"{self.idrac_manage_servers}/Bios",
                                   do_async=do_async).data or {}
            name = bios.get("AttributeRegistry")
            if name:
                reg = self.base_query(f"{RedfishApi.Version}/Registries/{name}",
                                      do_async=do_async).data or {}
                for loc in reg.get("Location", []):
                    uri = loc.get("Uri") if isinstance(loc, dict) else None
                    if uri:
                        return uri
        except Exception:
            pass
        return dell_uri

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
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = self._resolve_registry_uri(do_async)
        try:
            cmd_result = self.base_query(target_api,
                                         filename=None,
                                         do_async=do_async,
                                         do_expanded=do_expanded)
        except Exception:
            return CommandResult([], None, None, None)
        filtered_result = []
        attribute_names = None

        # Tolerate a resource that has no registry entries (e.g. registry not
        # reachable on this host) instead of raising a KeyError.
        registry = (cmd_result.data or {}).get('RegistryEntries')
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
