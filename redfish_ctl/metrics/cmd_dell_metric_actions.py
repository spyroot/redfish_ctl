"""Preview or invoke Dell MetricService Redfish actions.

    redfish_ctl dell-metric-actions
    redfish_ctl dell-metric-actions --action control-metrics
    redfish_ctl dell-metric-actions --action export-thermal-history \
        --share-address 192.0.2.10 --share-name thermal --confirm

The command resolves Dell's ``DellMetricService`` from ComputerSystem OEM links,
lists advertised metric-action targets by default, and previews selected
actions unless ``--confirm`` is passed. Network-share passwords are read from an
environment variable or file and masked in preview payloads.

Author Mus spyroot@gmail.com
"""
import os
from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_METRIC_SERVICE_FALLBACK = (
    f"{RedfishApi.Version}/Systems/System.Embedded.1/Oem/Dell/DellMetricService"
)
_REDACTED = "********"


@dataclass(frozen=True)
class _MetricActionSpec:
    """Selector metadata for one Dell MetricService action."""

    selector: str
    full_type: str
    action_name: str
    description: str
    requires_export_payload: bool = False


_ACTION_SPECS = {
    "control-metrics": _MetricActionSpec(
        selector="control-metrics",
        full_type="#DellMetricService.ControlMetrics",
        action_name="ControlMetrics",
        description="reset Dell metric collection state",
    ),
    "export-thermal-history": _MetricActionSpec(
        selector="export-thermal-history",
        full_type="#DellMetricService.ExportThermalHistory",
        action_name="ExportThermalHistory",
        description="export Dell thermal history to a network share",
        requires_export_payload=True,
    ),
}


class DellMetricActions(RedfishManagerBase,
                        scm_type=ApiRequestType.DellMetricActions,
                        name="dell-metric-actions",
                        metaclass=Singleton):
    """Discover and invoke Dell MetricService actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-metric-actions command."""
        super(DellMetricActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-metric-actions`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="Dell metric action to preview or invoke; omit to list targets",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DellMetricService URI when more than one target exists",
        )
        cmd_parser.add_argument(
            "--metric-collection-enabled",
            dest="metric_collection_enabled",
            default="Reset",
            help="ControlMetrics MetricCollectionEnabled payload value",
        )
        cmd_parser.add_argument(
            "--file-type",
            dest="file_type",
            default="CSV",
            help="ExportThermalHistory FileType value, usually CSV or XML",
        )
        cmd_parser.add_argument(
            "--share-type",
            dest="share_type",
            default="NFS",
            help="ExportThermalHistory ShareType value, usually NFS or CIFS",
        )
        cmd_parser.add_argument(
            "--share-address",
            dest="share_address",
            default=None,
            help="network share host or address for ExportThermalHistory",
        )
        cmd_parser.add_argument(
            "--share-name",
            dest="share_name",
            default=None,
            help="network share name or path for ExportThermalHistory",
        )
        cmd_parser.add_argument(
            "--file-name",
            dest="file_name",
            default=None,
            help="optional thermal-history export file name",
        )
        cmd_parser.add_argument(
            "--share-username",
            dest="share_username",
            default=None,
            help="optional network share username",
        )
        share_password = cmd_parser.add_mutually_exclusive_group(required=False)
        share_password.add_argument(
            "--share-password-env",
            dest="share_password_env",
            default=None,
            help="environment variable containing the network share password",
        )
        share_password.add_argument(
            "--share-password-file",
            dest="share_password_file",
            default=None,
            help="file containing the network share password",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the selected metric action instead of previewing it",
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
            "dell-metric-actions",
            "command run Dell MetricService actions",
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

    def _metric_service_uris(self, do_async):
        """Return DellMetricService URIs in discovery-first order.

        :param do_async: run supporting queries asynchronously when True.
        :return: de-duplicated DellMetricService URI candidates.
        """
        uris = []
        try:
            system_uris = self.discover_computer_system_ids() or []
        except Exception:
            system_uris = []
        for system_uri in system_uris:
            system = self._get(system_uri, do_async)
            linked = self._link(self._links_oem_dell(system), "DellMetricService")
            if linked:
                uris.append(linked)
            uris.append(f"{system_uri.rstrip('/')}/Oem/Dell/DellMetricService")
        uris.append(_METRIC_SERVICE_FALLBACK)

        seen = set()
        ordered = []
        for uri in uris:
            if uri and uri not in seen:
                seen.add(uri)
                ordered.append(uri)
        return ordered

    def _discover_rows(self, do_async):
        """Discover available Dell MetricService actions.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of available metric-action rows.
        """
        rows = []
        for service_uri in self._metric_service_uris(do_async):
            service = self._get(service_uri, do_async)
            if not service:
                continue
            actions = self.discover_redfish_actions(self, service)
            targets = self._flatten_action_targets(service)
            for spec in _ACTION_SPECS.values():
                target = targets.get(spec.full_type)
                if not target:
                    continue
                action = actions.get(spec.action_name)
                rows.append({
                    "Action": spec.selector,
                    "FullType": spec.full_type,
                    "Resource": service_uri,
                    "Target": target,
                    "Description": spec.description,
                    "AllowableValues": getattr(action, "args", {}) or {},
                })
        return rows

    @staticmethod
    def _matches(rows, action, resource_uri):
        """Filter discovered rows by action selector and optional resource URI.

        :param rows: discovered Dell MetricService rows.
        :param action: selected action name.
        :param resource_uri: optional service URI selector.
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
    def _read_secret(env_name=None, file_name=None, label="secret"):
        """Read secret material from an environment variable or file.

        :param env_name: environment variable whose value should be used.
        :param file_name: file path whose trimmed content should be used.
        :param label: human-readable secret label for validation errors.
        :return: secret string, or None when neither source is provided.
        :raises InvalidArgument: when a requested source is missing or empty.
        """
        if env_name:
            value = os.environ.get(env_name)
            if value is None:
                raise InvalidArgument(f"{label} env var is not set: {env_name}")
            if value == "":
                raise InvalidArgument(f"{label} env var is empty: {env_name}")
            return value
        if file_name:
            path = Path(file_name).expanduser()
            if not path.exists():
                raise InvalidArgument(f"{label} file does not exist: {file_name}")
            value = path.read_text().strip()
            if not value:
                raise InvalidArgument(f"{label} file is empty: {file_name}")
            return value
        return None

    @staticmethod
    def _control_payload(metric_collection_enabled):
        """Build a DellMetricService.ControlMetrics payload.

        :param metric_collection_enabled: desired MetricCollectionEnabled value.
        :return: JSON payload for the action.
        :raises InvalidArgument: when the value is empty.
        """
        value = (metric_collection_enabled or "").strip()
        if not value:
            raise InvalidArgument("MetricCollectionEnabled cannot be empty")
        return {"MetricCollectionEnabled": value}

    @classmethod
    def _export_payload(cls,
                        file_type,
                        share_type,
                        share_address,
                        share_name,
                        file_name=None,
                        share_username=None,
                        share_password_env=None,
                        share_password_file=None):
        """Build a DellMetricService.ExportThermalHistory payload.

        :param file_type: Dell FileType value, usually CSV or XML.
        :param share_type: Dell ShareType value, usually NFS or CIFS.
        :param share_address: network share host or address.
        :param share_name: network share name or path.
        :param file_name: optional destination file name.
        :param share_username: optional network share username.
        :param share_password_env: environment variable holding the share password.
        :param share_password_file: file holding the share password.
        :return: JSON payload for the action.
        :raises InvalidArgument: when a required value is empty or a secret source
            is requested but unavailable.
        """
        payload = {
            "FileType": (file_type or "").strip(),
            "ShareType": (share_type or "").strip(),
            "IPAddress": (share_address or "").strip(),
            "ShareName": (share_name or "").strip(),
        }
        missing = [name for name, value in payload.items() if not value]
        if missing:
            raise InvalidArgument(
                "ExportThermalHistory requires: " + ", ".join(missing)
            )
        optional = {
            "FileName": file_name,
            "UserName": share_username,
            "Password": cls._read_secret(
                share_password_env,
                share_password_file,
                "share password",
            ),
        }
        for key, value in optional.items():
            if value is not None and str(value).strip():
                payload[key] = str(value).strip()
        return payload

    @staticmethod
    def _redacted_payload(payload):
        """Return a preview-safe copy of a payload.

        :param payload: payload that may contain secret material.
        :return: payload copy with secret-bearing keys masked.
        """
        redacted = dict(payload)
        for key in ("Password", "SharePassword"):
            if key in redacted:
                redacted[key] = _REDACTED
        return redacted

    def _payload_for(self, spec, preview, **kwargs):
        """Build the selected action payload.

        :param spec: selected action metadata.
        :param preview: when True, mask secrets in the returned payload.
        :param kwargs: CLI arguments used to build the action payload.
        :return: JSON payload for ``spec``.
        """
        if spec.selector == "control-metrics":
            return self._control_payload(kwargs.get("metric_collection_enabled"))
        payload = self._export_payload(
            kwargs.get("file_type"),
            kwargs.get("share_type"),
            kwargs.get("share_address"),
            kwargs.get("share_name"),
            file_name=kwargs.get("file_name"),
            share_username=kwargs.get("share_username"),
            share_password_env=kwargs.get("share_password_env"),
            share_password_file=kwargs.get("share_password_file"),
        )
        return self._redacted_payload(payload) if preview else payload

    def execute(self,
                action: Optional[str] = None,
                resource_uri: Optional[str] = None,
                metric_collection_enabled: Optional[str] = "Reset",
                file_type: Optional[str] = "CSV",
                share_type: Optional[str] = "NFS",
                share_address: Optional[str] = None,
                share_name: Optional[str] = None,
                file_name: Optional[str] = None,
                share_username: Optional[str] = None,
                share_password_env: Optional[str] = None,
                share_password_file: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke Dell MetricService actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param resource_uri: optional service URI to disambiguate multiple targets.
        :param metric_collection_enabled: ControlMetrics payload value.
        :param file_type: ExportThermalHistory FileType payload value.
        :param share_type: ExportThermalHistory ShareType payload value.
        :param share_address: export network share host or address.
        :param share_name: export network share name or path.
        :param file_name: optional export file name.
        :param share_username: optional network share username.
        :param share_password_env: environment variable containing the share password.
        :param share_password_file: file containing the share password.
        :param confirm: POST the selected action when True.
        :param dry_run: force preview mode even when ``confirm`` is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying reads/POST on the async path.
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
                f"Dell MetricService action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple Dell MetricService action targets found; pass --resource-uri",
            )

        spec = _ACTION_SPECS[action]
        preview = bool(dry_run) or not bool(confirm)
        payload = self._payload_for(
            spec,
            preview,
            metric_collection_enabled=metric_collection_enabled,
            file_type=file_type,
            share_type=share_type,
            share_address=share_address,
            share_name=share_name,
            file_name=file_name,
            share_username=share_username,
            share_password_env=share_password_env,
            share_password_file=share_password_file,
        )
        row = matches[0]
        return self.invoke_action(
            row["Resource"],
            spec.action_name,
            payload=payload,
            full_action_type=spec.full_type,
            do_async=do_async,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
