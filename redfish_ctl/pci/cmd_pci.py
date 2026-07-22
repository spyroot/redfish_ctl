"""PCI device query command.

Command provides the option to retrieve firmware setting from a Redfish endpoint and serialize
back as caller as JSON, YAML, and XML. In addition, it automatically
registers to the command line ctl tool. Similarly to the rest command caller can save
to a file and consume asynchronously or synchronously.

    redfish_ctl pci PCIeDevices

Author Mus spyroot@gmail.com
"""
import asyncio
from abc import abstractmethod
from typing import Optional

from tqdm import tqdm

from ..cmd_utils import save_if_needed
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class PciDeviceQuery(IDracManager,
                     scm_type=ApiRequestType.PciDeviceQuery,
                     name='pci_device_query',
                     metaclass=Singleton):
    """
    Command implementation to get pci device and pci functions.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the pci command."""
        super(PciDeviceQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('pci_type', choices=['PCIeDevices', 'PCIeFunctions'],
                                default="PCIeDevices",
                                help="either pci device or pci function")

        help_text = "command fetch the pci device or function"
        return cmd_parser, "pci", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                pci_type: Optional[str] = "PCIeDevices",
                **kwargs) -> CommandResult:
        """Query PCI device or function from a Redfish endpoint.
        :param verbose:
        :param do_async:
        :param filename: if filename indicate call will save the response to this file.
        :param data_type: a data serialized back.
        :param pci_type: PCIeDevices or  PCIeFunctions
        :return: in data type json will return json
        """

        if verbose:
            self.logger.debug(f"cmd args data_type: {data_type} "
                  f"filename:{filename} do_async:{do_async} dev:{pci_type}")
            self.logger.debug(f"the rest of args: {kwargs}")

        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        r = f"{self._default_method}{self.idrac_ip}{self.idrac_manage_servers}" \
            f"?$select={pci_type}"

        try:
            if not do_async:
                response = self.api_get_call(r, headers)
                self.default_error_handler(response)
            else:
                loop = self._event_loop()
                response = loop.run_until_complete(
                    self.api_async_get_until_complete(r, headers))
            data = response.json()
        except Exception:
            data = {}

        pci_data = []
        # Dell returns PCIeDevices/Functions as an array of links off the System;
        # standard Redfish exposes a single collection link (resolve its Members).
        endpoints = data.get(pci_type) if isinstance(data, dict) else None
        if isinstance(endpoints, dict) and endpoints.get("@odata.id"):
            coll = self.base_query(endpoints["@odata.id"], do_async=do_async).data or {}
            endpoints = coll.get("Members", [])
        if isinstance(endpoints, list):
            with tqdm(total=len(endpoints)) as pbar:
                for ep in endpoints:
                    uri = ep.get("@odata.id") if isinstance(ep, dict) else None
                    if uri:
                        try:
                            pci_data.append(self.base_query(uri, do_async=do_async).data)
                        except Exception:
                            pass
                    pbar.update(1)

        # Dell hangs PCIeDevices off the ComputerSystem; iLO/Supermicro hang them
        # off Chassis. Fall back to the Chassis layout when the System select was
        # empty, so the command is not Dell-only.
        if not pci_data:
            pci_data = self._pci_from_chassis(pci_type, do_async)

        save_if_needed(filename, pci_data)
        return CommandResult(pci_data, None, None,  None)

    def _pci_from_chassis(self, pci_type, do_async):
        """Collect PCIe devices/functions from the Chassis layout, tolerantly.

        Walks /redfish/v1/Chassis -> each chassis PCIeDevices collection -> each
        device (and, for PCIeFunctions, that device's PCIeFunctions collection).
        Skips a chassis or leaf that is missing/unreachable rather than failing.

        :param pci_type: PCIeDevices or PCIeFunctions; PCIeFunctions descends one
            level further into each device's functions collection.
        :param do_async: forwarded to base_query so the walk can run async.
        :return: list of PCIe device or function bodies (empty if none found).
        """
        def get(uri):
            """Fetch the resource body at ``uri``, or {} on any error.

            :param uri: Redfish resource path to GET.
            :return: the resource body dict, or an empty dict on failure.
            """
            try:
                return self.base_query(uri, do_async=do_async).data or {}
            except Exception:
                return {}

        def members(data):
            """Return the ``@odata.id`` strings from a collection body.

            :param data: a Redfish collection body (dict carrying ``Members``).
            :return: list of member ``@odata.id`` strings.
            """
            return [m["@odata.id"] for m in (data.get("Members") or [])
                    if isinstance(m, dict) and isinstance(m.get("@odata.id"), str)]

        out = []
        for chassis_uri in members(get(f"{RedfishApi.Version}/Chassis")):
            link = get(chassis_uri).get("PCIeDevices")
            coll_uri = link.get("@odata.id") if isinstance(link, dict) else None
            if not coll_uri:
                continue
            for dev_uri in members(get(coll_uri)):
                dev = get(dev_uri)
                if pci_type == "PCIeFunctions":
                    fn_link = dev.get("PCIeFunctions")
                    fn_coll = fn_link.get("@odata.id") if isinstance(fn_link, dict) else None
                    for fn_uri in members(get(fn_coll)) if fn_coll else []:
                        out.append(get(fn_uri))
                elif dev:
                    out.append(dev)
        return out
