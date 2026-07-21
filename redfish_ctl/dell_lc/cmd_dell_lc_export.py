"""Export Dell Lifecycle Controller data through DellLCService actions.

    redfish_ctl dell-lc-export
    redfish_ctl dell-lc-export --export lc-log --share-type NFS \
        --share-name /exports/lc --dry_run
    redfish_ctl dell-lc-export --export hw-inventory --share-type HTTPS \
        --share-name reports --confirm

The command resolves DellLCService from the Manager OEM path and falls back to
the older Dell fixture path. Export actions can write potentially sensitive
support data to a local or network target, so selected exports preview by default
and only POST when ``--confirm`` is supplied.

Author Mus spyroot@gmail.com
"""
import os
from abc import abstractmethod
from pathlib import Path
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton

_DELL_LC_EXPORTS = {
    "complete-lc-log": "#DellLCService.ExportCompleteLCLog",
    "epsa-diagnostics": "#DellLCService.ExportePSADiagnosticsResult",
    "factory-configuration": "#DellLCService.ExportFactoryConfiguration",
    "hw-inventory": "#DellLCService.ExportHWInventory",
    "lc-log": "#DellLCService.ExportLCLog",
    "server-screenshot": "#DellLCService.ExportServerScreenShot",
    "svg": "#DellLCService.ExportSVGFile",
    "tech-support-report": "#DellLCService.ExportTechSupportReport",
    "video-log": "#DellLCService.ExportVideoLog",
}


class DellLcExport(RedfishManagerBase,
                   scm_type=ApiRequestType.DellLcExport,
                   name="dell-lc-export",
                   metaclass=Singleton):
    """Export Dell Lifecycle Controller data through discovered DellLCService actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-lc-export command."""
        super(DellLcExport, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-lc-export`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--export",
            required=False,
            dest="export_name",
            choices=tuple(sorted(_DELL_LC_EXPORTS)),
            default=None,
            help="LC export action to run; omit to list discovered export targets",
        )
        cmd_parser.add_argument(
            "--share-type",
            required=False,
            dest="share_type",
            type=str,
            default=None,
            help="ShareType value for export actions that write to a share",
        )
        cmd_parser.add_argument(
            "--ip-address",
            required=False,
            dest="ip_address",
            type=str,
            default=None,
            help="network share host or address for actions that require IPAddress",
        )
        cmd_parser.add_argument(
            "--share-name",
            required=False,
            dest="share_name",
            type=str,
            default=None,
            help="network share name or local export destination",
        )
        cmd_parser.add_argument(
            "--file-name",
            required=False,
            dest="file_name",
            type=str,
            default=None,
            help="optional output file name for the export payload",
        )
        cmd_parser.add_argument(
            "--username",
            required=False,
            dest="share_username",
            type=str,
            default=None,
            help="optional network share username",
        )
        password_group = cmd_parser.add_mutually_exclusive_group(required=False)
        password_group.add_argument(
            "--password-env",
            required=False,
            dest="share_password_env",
            type=str,
            default=None,
            help="environment variable containing the share password",
        )
        password_group.add_argument(
            "--password-file",
            required=False,
            dest="share_password_file",
            type=str,
            default=None,
            help="file containing the share password",
        )
        cmd_parser.add_argument(
            "--workgroup",
            required=False,
            dest="workgroup",
            type=str,
            default=None,
            help="optional CIFS workgroup",
        )
        cmd_parser.add_argument(
            "--ignore-cert-warning",
            required=False,
            dest="ignore_cert_warning",
            type=str,
            default=None,
            help="IgnoreCertWarning value when the export action advertises it",
        )
        cmd_parser.add_argument(
            "--proxy-support",
            required=False,
            dest="proxy_support",
            type=str,
            default=None,
            help="ProxySupport value when the export action advertises it",
        )
        cmd_parser.add_argument(
            "--proxy-type",
            required=False,
            dest="proxy_type",
            type=str,
            default=None,
            help="ProxyType value when the export action advertises it",
        )
        cmd_parser.add_argument(
            "--xml-schema",
            required=False,
            dest="xml_schema",
            type=str,
            default=None,
            help="XMLSchema value for HW inventory export",
        )
        cmd_parser.add_argument(
            "--file-type",
            required=False,
            dest="file_type",
            type=str,
            default=None,
            help="FileType value for screenshot or video export",
        )
        cmd_parser.add_argument(
            "--data-selector",
            action="append",
            required=False,
            dest="data_selectors",
            default=None,
            help="DataSelectorArrayIn entry; repeat for multiple selectors",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the selected DellLCService export POST",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show it without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-lc-export",
            "command export Dell Lifecycle Controller data",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property.

        :param data: resource body holding the link.
        :param key: property name whose ``@odata.id`` to extract.
        :return: link target URI, or None when absent.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _manager_id(manager_uri):
        """Return the Manager id from a Manager URI.

        :param manager_uri: Manager resource URI.
        :return: last path segment, or None when malformed.
        """
        value = (manager_uri or "").rstrip("/")
        return value.rsplit("/", 1)[-1] if value else None

    def _get(self, uri, do_async):
        """Read a Redfish resource, returning None when the GET is not usable.

        :param uri: Redfish resource URI to fetch.
        :param do_async: issue the read through the async path when True.
        :return: parsed JSON object or None.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _append_unique(items, value):
        """Append a non-empty value to ``items`` once.

        :param items: list being built.
        :param value: candidate string value.
        :return: None.
        """
        if value and value not in items:
            items.append(value)

    def _candidate_lc_uris(self, do_async):
        """Return candidate DellLCService URIs for modern and legacy layouts.

        :param do_async: issue discovery reads through the async path when True.
        :return: ordered list of candidate DellLCService URIs.
        """
        candidates = []
        try:
            managers = self.discover_manager_ids() or []
        except Exception:
            managers = []
        for manager_uri in managers:
            manager = self._get(manager_uri, do_async) or {}
            oem_dell = (manager.get("Oem") or {}).get("Dell")
            if isinstance(oem_dell, dict):
                self._append_unique(
                    candidates, self._link(oem_dell, "DellLCService"))
            manager_id = self._manager_id(manager_uri)
            if manager_id:
                self._append_unique(
                    candidates,
                    f"{manager_uri.rstrip('/')}/Oem/Dell/DellLCService",
                )
                self._append_unique(
                    candidates,
                    f"/redfish/v1/Dell/Managers/{manager_id}/DellLCService",
                )
        self._append_unique(
            candidates,
            "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService",
        )
        self._append_unique(
            candidates, "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService")
        return candidates

    def _lc_service(self, do_async):
        """Read the first available DellLCService resource.

        :param do_async: issue discovery reads through the async path when True.
        :return: tuple of ``(uri, body)`` or ``(None, CommandResult)`` on error.
        """
        candidates = self._candidate_lc_uris(do_async)
        for uri in candidates:
            service = self._get(uri, do_async)
            actions = service.get("Actions") if isinstance(service, dict) else None
            if isinstance(actions, dict) and any(
                    name.startswith("#DellLCService.") for name in actions):
                return uri, service
        return None, CommandResult(
            {"candidates": candidates}, None, None,
            "DellLCService is not available on this Redfish endpoint")

    @staticmethod
    def _allowed_args(action):
        """Return advertised allowable values for an action.

        :param action: discovered RedfishAction.
        :return: dict of argument name to sorted allowable values.
        """
        args = getattr(action, "args", None) or {}
        return {key: sorted(values or []) for key, values in sorted(args.items())}

    def _export_metadata(self, do_async):
        """Return discovered LC export target metadata.

        :param do_async: issue the service query through the async path when True.
        :return: CommandResult containing the export action table or an error.
        """
        uri, service = self._lc_service(do_async)
        if isinstance(service, CommandResult):
            return service
        actions = self.discover_redfish_actions(self, service)
        full_targets = self._flatten_action_targets(service)
        exports = []
        for export_name, full_type in _DELL_LC_EXPORTS.items():
            target = full_targets.get(full_type)
            if target is None:
                continue
            action_name = full_type.rsplit(".", 1)[-1]
            exports.append({
                "export": export_name,
                "action": full_type,
                "target": target,
                "allowed": self._allowed_args(actions.get(action_name)),
            })
        return CommandResult({
            "lc_service": uri,
            "export_actions": exports,
        }, actions, None, None)

    @staticmethod
    def _optional_payload_value(value):
        """Strip optional string payload values and drop empty values.

        :param value: optional Redfish action payload value.
        :return: stripped string, original non-string value, or None.
        """
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @staticmethod
    def _password_from_source(password_env=None, password_file=None):
        """Read a share password from a named environment variable or file.

        :param password_env: environment variable name to read.
        :param password_file: path to a file containing the password.
        :return: password string, or None when no source is provided.
        :raises InvalidArgument: when the source is invalid or unavailable.
        """
        if password_env and password_file:
            raise InvalidArgument(
                "use only one of --password-env or --password-file"
            )
        if password_env is not None:
            env_name = password_env.strip()
            if not env_name:
                raise InvalidArgument("password environment variable name cannot be empty")
            if env_name not in os.environ:
                raise InvalidArgument(f"password environment variable '{env_name}' is not set")
            return os.environ[env_name]
        if password_file is not None:
            path = Path(password_file).expanduser()
            try:
                return path.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as exc:
                raise InvalidArgument(
                    f"failed to read password file '{path}': {exc}"
                ) from exc
        return None

    @staticmethod
    def _payload(**kwargs):
        """Build a DellLCService export payload from supplied optional fields.

        :param kwargs: normalized command arguments.
        :return: JSON-serializable action payload.
        """
        fields = {
            "ShareType": kwargs.get("share_type"),
            "IPAddress": kwargs.get("ip_address"),
            "ShareName": kwargs.get("share_name"),
            "FileName": kwargs.get("file_name"),
            "UserName": kwargs.get("share_username"),
            "Password": kwargs.get("share_password"),
            "Workgroup": kwargs.get("workgroup"),
            "IgnoreCertWarning": kwargs.get("ignore_cert_warning"),
            "ProxySupport": kwargs.get("proxy_support"),
            "ProxyType": kwargs.get("proxy_type"),
            "XMLSchema": kwargs.get("xml_schema"),
            "FileType": kwargs.get("file_type"),
        }
        payload = {
            key: DellLcExport._optional_payload_value(value)
            for key, value in fields.items()
        }
        selectors = [
            selector.strip() for selector in kwargs.get("data_selectors") or []
            if isinstance(selector, str) and selector.strip()
        ]
        if selectors:
            payload["DataSelectorArrayIn"] = selectors
        return {key: value for key, value in payload.items() if value is not None}

    @staticmethod
    def _selected_export(export_name, exports):
        """Resolve an export choice to its discovered action metadata.

        :param export_name: requested export choice.
        :param exports: discovered export metadata rows.
        :return: selected metadata row.
        :raises InvalidArgument: when the export is unavailable.
        """
        selected = next(
            (item for item in exports if item["export"] == export_name),
            None,
        )
        if selected is None:
            available = [item["export"] for item in exports]
            raise InvalidArgument(
                f"DellLCService export '{export_name}' is not available; "
                f"available: {available}")
        return selected

    @staticmethod
    def _redact_password(result):
        """Mask a share password in returned preview payloads.

        :param result: CommandResult from ``invoke_action``.
        :return: CommandResult with any payload password redacted.
        """
        if not isinstance(result.data, dict):
            return result
        payload = result.data.get("payload")
        if isinstance(payload, dict) and "Password" in payload:
            payload = dict(payload)
            payload["Password"] = "********"
            result.data["payload"] = payload
        return result

    def execute(self,
                export_name: Optional[str] = None,
                share_type: Optional[str] = None,
                ip_address: Optional[str] = None,
                share_name: Optional[str] = None,
                file_name: Optional[str] = None,
                share_username: Optional[str] = None,
                share_password_env: Optional[str] = None,
                share_password_file: Optional[str] = None,
                workgroup: Optional[str] = None,
                ignore_cert_warning: Optional[str] = None,
                proxy_support: Optional[str] = None,
                proxy_type: Optional[str] = None,
                xml_schema: Optional[str] = None,
                file_type: Optional[str] = None,
                data_selectors: Optional[list[str]] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List export targets, preview a selected export, or fire it.

        With no ``--export`` the command lists discovered LC export actions
        WITHOUT mutating. With an export choice it invokes the advertised action;
        because LC exports can write support data to a target, the POST only
        fires with ``--confirm``. ``--dry_run`` overrides confirmation.

        :param export_name: export action choice; None lists discovered targets.
        :param share_type: optional ShareType payload value.
        :param ip_address: optional IPAddress payload value.
        :param share_name: optional ShareName payload value.
        :param file_name: optional FileName payload value.
        :param share_username: optional UserName payload value.
        :param share_password_env: environment variable holding an optional share password.
        :param share_password_file: file holding an optional share password.
        :param workgroup: optional Workgroup payload value.
        :param ignore_cert_warning: optional IgnoreCertWarning payload value.
        :param proxy_support: optional ProxySupport payload value.
        :param proxy_type: optional ProxyType payload value.
        :param xml_schema: optional XMLSchema payload value.
        :param file_type: optional FileType payload value.
        :param data_selectors: optional DataSelectorArrayIn entries.
        :param confirm: authorize the selected export POST to actually fire.
        :param dry_run: resolve the target and show the payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST through the async path.
        :return: CommandResult with target metadata, a blocked/dry-run preview,
            or the POST result.
        """
        metadata = self._export_metadata(do_async)
        if metadata.error or export_name is None:
            return metadata
        exports = metadata.data.get("export_actions", [])
        selected = self._selected_export(export_name, exports)
        full_action = selected["action"]
        action_name = full_action.rsplit(".", 1)[-1]
        payload = self._payload(
            share_type=share_type,
            ip_address=ip_address,
            share_name=share_name,
            file_name=file_name,
            share_username=share_username,
            share_password=self._password_from_source(
                share_password_env,
                share_password_file,
            ),
            workgroup=workgroup,
            ignore_cert_warning=ignore_cert_warning,
            proxy_support=proxy_support,
            proxy_type=proxy_type,
            xml_schema=xml_schema,
            file_type=file_type,
            data_selectors=data_selectors,
        )
        preview_only = bool(dry_run) or not bool(confirm)
        result = self.invoke_action(
            metadata.data["lc_service"],
            action_name,
            payload=payload,
            full_action_type=full_action,
            do_async=do_async,
            expected_status=202,
            dry_run=preview_only,
            confirm=True,
        )
        if (
                not confirm
                and not dry_run
                and result.error is None
                and isinstance(result.data, dict)):
            result.data["blocked"] = "DellLCService export requires --confirm"
        return self._redact_password(result)
