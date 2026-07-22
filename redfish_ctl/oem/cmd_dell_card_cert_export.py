"""Discover and query Dell iDRAC card certificate export actions.

    redfish_ctl dell-card-cert-export
    redfish_ctl dell-card-cert-export --action factory-identity --query
    redfish_ctl dell-card-cert-export --action export-cert \\
        --certificate-type KMS_SERVER_CA --query

The command discovers certificate export targets from
``Links.Oem.Dell.DelliDRACCardService`` on Manager resources. A selected action
previews by default; ``--query`` is required before the read-only POST is sent.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton


@dataclass(frozen=True)
class _DellCardCertExportSpec:
    """Static selector metadata for one Dell card certificate export action."""

    selector: str
    full_type: str
    action_name: str
    description: str
    payload_field: Optional[str] = None
    cli_arg: Optional[str] = None


_ACTION_SPECS = {
    "export-cert": _DellCardCertExportSpec(
        selector="export-cert",
        full_type="#DelliDRACCardService.ExportCertificate",
        action_name="ExportCertificate",
        description="export a Dell card-service certificate by certificate type",
        payload_field="CertificateType",
        cli_arg="--certificate-type",
    ),
    "export-ssl-cert": _DellCardCertExportSpec(
        selector="export-ssl-cert",
        full_type="#DelliDRACCardService.ExportSSLCertificate",
        action_name="ExportSSLCertificate",
        description="export an iDRAC SSL certificate by SSL certificate type",
        payload_field="SSLCertType",
        cli_arg="--ssl-cert-type",
    ),
    "factory-identity": _DellCardCertExportSpec(
        selector="factory-identity",
        full_type="#DelliDRACCardService.FactoryIdentityExportCertificate",
        action_name="FactoryIdentityExportCertificate",
        description="export the factory identity certificate",
    ),
}


class DellCardCertExport(IDracManager,
                         scm_type=ApiRequestType.DellCardCertExport,
                         name="dell-card-cert-export",
                         metaclass=Singleton):
    """Discover and query Dell iDRAC card certificate export actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-card-cert-export command."""
        super(DellCardCertExport, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-card-cert-export`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help=(
                "Dell card certificate export action to preview or query; "
                "omit to list"
            ),
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DelliDRACCardService URI when more than one target exists",
        )
        cmd_parser.add_argument(
            "--certificate-type",
            dest="certificate_type",
            default=None,
            help="CertificateType payload for export-cert",
        )
        cmd_parser.add_argument(
            "--ssl-cert-type",
            dest="ssl_cert_type",
            default=None,
            help="SSLCertType payload for export-ssl-cert",
        )
        cmd_parser.add_argument(
            "--query",
            action="store_true",
            default=False,
            help="POST the selected read-only export action instead of previewing it",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and payload without POSTing",
        )
        return (
            cmd_parser,
            "dell-card-cert-export",
            "command export Dell iDRAC card certificates",
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
        :param do_async: run the query asynchronously when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _card_service_uris(self, do_async):
        """Return DelliDRACCardService resource URIs for every manager.

        :param do_async: run manager queries asynchronously when True.
        :return: list of DelliDRACCardService URIs.
        """
        uris = []
        for manager_uri in self.discover_manager_ids() or []:
            manager = self._get(manager_uri, do_async)
            links_dell = self._links_oem_dell(manager)
            service_uri = self._link(links_dell, "DelliDRACCardService")
            if not service_uri:
                oem_dell = self._oem_dell(manager)
                service_uri = self._link(oem_dell, "DelliDRACCardService")
            if not service_uri:
                service_uri = f"{manager_uri.rstrip('/')}/Oem/Dell/DelliDRACCardService"
            uris.append(service_uri)
        return uris

    def _discover_rows(self, do_async):
        """Discover available Dell card certificate export actions.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of available export-action rows.
        """
        rows = []
        for service_uri in self._card_service_uris(do_async):
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

        :param rows: discovered Dell card export-action rows.
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

    @staticmethod
    def _payload_for(spec, certificate_type, ssl_cert_type):
        """Build the selected action payload.

        :param spec: selected action metadata.
        :param certificate_type: CertificateType value for export-cert.
        :param ssl_cert_type: SSLCertType value for export-ssl-cert.
        :return: JSON payload for the action.
        """
        if spec.payload_field is None:
            return {}
        value = (
            certificate_type
            if spec.payload_field == "CertificateType"
            else ssl_cert_type
        )
        if not value:
            raise ValueError(f"{spec.selector} requires {spec.cli_arg}")
        return {spec.payload_field: value}

    def execute(self,
                action: Optional[str] = None,
                resource_uri: Optional[str] = None,
                certificate_type: Optional[str] = None,
                ssl_cert_type: Optional[str] = None,
                query: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or query Dell card certificate export actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param resource_uri: optional service URI to disambiguate multiple targets.
        :param certificate_type: CertificateType payload for ``export-cert``.
        :param ssl_cert_type: SSLCertType payload for ``export-ssl-cert``.
        :param query: POST the selected read-only export action when True.
        :param dry_run: force preview mode even when ``query`` is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        rows = self._discover_rows(bool(do_async))
        if action is None:
            return CommandResult(rows, None, None, None)

        matches = self._matches(rows, action, resource_uri)
        if not matches:
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"Dell iDRAC card certificate export action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple Dell iDRAC card certificate export targets found; "
                "pass --resource-uri",
            )

        spec = _ACTION_SPECS[action]
        try:
            payload = self._payload_for(spec, certificate_type, ssl_cert_type)
        except ValueError as exc:
            return CommandResult({"available": rows}, None, None, str(exc))

        row = matches[0]
        return self.invoke_action(
            row["Resource"],
            spec.action_name,
            payload=payload,
            full_action_type=spec.full_type,
            do_async=do_async,
            dry_run=bool(dry_run) or not bool(query),
            confirm=False,
        )
