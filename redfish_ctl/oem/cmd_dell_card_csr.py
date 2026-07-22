"""Generate Dell iDRAC card-service certificate signing requests.

    redfish_ctl dell-card-csr
    redfish_ctl dell-card-csr --action factory-identity --confirm
    redfish_ctl dell-card-csr --action sekm --dry_run

The command discovers ``DelliDRACCardService`` from Manager OEM links when the
manager exposes them, otherwise it falls back to the standard Dell manager OEM
resource path. Selected CSR actions preview by default; ``--confirm`` is
required before POSTing.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_SERVICE_NAME = "DelliDRACCardService"
_DEFAULT_SERVICE_URI = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/{_SERVICE_NAME}"
)


@dataclass(frozen=True)
class _DellCardCsrSpec:
    """Static selector metadata for one Dell card-service CSR action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "factory-identity": _DellCardCsrSpec(
        selector="factory-identity",
        full_type="#DelliDRACCardService.FactoryIdentityCertificateGenerateCSR",
        action_name="FactoryIdentityCertificateGenerateCSR",
        description="generate a CSR for the Dell factory identity certificate",
    ),
    "sekm": _DellCardCsrSpec(
        selector="sekm",
        full_type="#DelliDRACCardService.GenerateSEKMCSR",
        action_name="GenerateSEKMCSR",
        description="generate a CSR for Dell SEKM certificate enrollment",
    ),
}


class DellCardCsr(IDracManager,
                  scm_type=ApiRequestType.DellCardCsr,
                  name="dell-card-csr",
                  metaclass=Singleton):
    """Discover and generate Dell card-service CSRs."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-card-csr command."""
        super(DellCardCsr, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-card-csr`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="Dell card-service CSR action to preview or run; omit to list",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DelliDRACCardService URI when discovery needs override",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the selected CSR action instead of previewing it",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target without POSTing",
        )
        return (
            cmd_parser,
            "dell-card-csr",
            "command generate Dell iDRAC card-service CSRs",
        )

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` link value from a Redfish object.

        :param data: Redfish resource body.
        :param key: link property name.
        :return: linked URI, or None when absent or malformed.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _oem_dell(data):
        """Return the ``Oem.Dell`` extension block from a resource.

        :param data: Redfish resource body.
        :return: Dell OEM block, or an empty dict.
        """
        oem = data.get("Oem") if isinstance(data, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    @staticmethod
    def _links_oem_dell(data):
        """Return the ``Links.Oem.Dell`` block from a resource.

        :param data: Redfish resource body.
        :return: Dell OEM links block, or an empty dict.
        """
        links = data.get("Links") if isinstance(data, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body, tolerating missing optional resources.

        :param uri: Redfish resource URI.
        :param do_async: issue the query on the async path when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _service_uris(self, do_async):
        """Return candidate DelliDRACCardService resource URIs.

        :param do_async: issue Manager queries on the async path when True.
        :return: ordered list of candidate service URIs.
        """
        uris = []
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            manager = self._get(manager_uri, do_async)
            service_uri = self._link(
                self._links_oem_dell(manager),
                _SERVICE_NAME,
            )
            if not service_uri:
                service_uri = self._link(self._oem_dell(manager), _SERVICE_NAME)
            if service_uri:
                uris.append(service_uri)
        if not uris:
            uris.append(_DEFAULT_SERVICE_URI)
        return list(dict.fromkeys(uri.rstrip("/") for uri in uris))

    def _discover_rows(self, do_async, resource_uri=None):
        """Discover Dell card-service CSR action targets.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DelliDRACCardService URI.
        :return: list of discovered CSR target rows.
        """
        uris = [resource_uri.rstrip("/")] if resource_uri else self._service_uris(
            do_async
        )
        rows = []
        for service_uri in dict.fromkeys(uris):
            service = self._get(service_uri, do_async)
            targets = self._flatten_action_targets(service)
            for spec in _ACTION_SPECS.values():
                target = targets.get(spec.full_type)
                if target:
                    rows.append({
                        "Action": spec.selector,
                        "FullType": spec.full_type,
                        "Resource": service_uri,
                        "Target": target,
                        "Description": spec.description,
                    })
        return rows

    @staticmethod
    def _matches(rows, action, resource_uri):
        """Filter discovered rows by action selector and optional resource URI.

        :param rows: discovered Dell card-service CSR rows.
        :param action: selected action name.
        :param resource_uri: optional resource URI selector.
        :return: matching rows.
        """
        matches = [row for row in rows if row["Action"] == action]
        if resource_uri:
            normalized = resource_uri.rstrip("/")
            matches = [
                row for row in matches
                if row["Resource"].rstrip("/") == normalized
            ]
        return matches

    def execute(self,
                action: Optional[str] = None,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or invoke Dell card-service CSR actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param resource_uri: optional service URI to disambiguate multiple targets.
        :param confirm: authorize a POST. Without this the selected action previews.
        :param dry_run: force a preview even with ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying Redfish reads/POST on the async path.
        :return: CommandResult with discovered targets, preview, POST result, or error.
        """
        rows = self._discover_rows(bool(do_async), resource_uri=resource_uri)
        if action is None:
            return CommandResult({"csr_targets": rows}, None, None, None)
        if action not in _ACTION_SPECS:
            return CommandResult(
                {"available": sorted(_ACTION_SPECS)},
                None,
                None,
                f"unknown Dell card-service CSR action: {action}",
            )

        matches = self._matches(rows, action, resource_uri)
        if not matches:
            return CommandResult(
                {"action": _ACTION_SPECS[action].full_type, "available": rows},
                None,
                None,
                f"Dell card-service CSR action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple Dell card-service CSR targets found; pass --resource-uri",
            )

        spec = _ACTION_SPECS[action]
        result = self.invoke_action(
            matches[0]["Resource"],
            spec.action_name,
            payload={},
            full_action_type=spec.full_type,
            do_async=do_async,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        if not confirm and isinstance(result.data, dict):
            result.data["requires_confirm"] = True
            result.data["blocked"] = "Dell card-service CSR generation requires --confirm"
        return result
