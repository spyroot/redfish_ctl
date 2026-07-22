"""Export the last Dell SupportAssist collection through DellLCService.

    redfish_ctl dell-lc-supportassist-export
    redfish_ctl dell-lc-supportassist-export --share-type NFS --share-name /exports --dry_run
    redfish_ctl dell-lc-supportassist-export --share-type HTTPS --ip-address repo.example --confirm

The command resolves ``#DellLCService.SupportAssistExportLastCollection`` from
the Dell Lifecycle Controller service. Exporting a collection can send support
data to a local or network share, so the action previews by default and only
POSTs when ``--confirm`` is provided.
"""
import os
from abc import abstractmethod
from pathlib import Path
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton

_SUPPORTASSIST_EXPORT_ACTION = "#DellLCService.SupportAssistExportLastCollection"


class DellLcSupportAssistExport(IDracManager,
                                scm_type=ApiRequestType.DellLcSupportAssistExport,
                                name="dell-lc-supportassist-export",
                                metaclass=Singleton):
    """Preview or invoke DellLCService.SupportAssistExportLastCollection."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-lc-supportassist-export command."""
        super(DellLcSupportAssistExport, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-lc-supportassist-export`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--share-type",
            required=False,
            dest="share_type",
            type=str,
            default=None,
            help="ShareType value for the export action",
        )
        cmd_parser.add_argument(
            "--ip-address",
            required=False,
            dest="ip_address",
            type=str,
            default=None,
            help="network share host or address",
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
            help="optional output file name for the export",
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
            help="IgnoreCertWarning value when HTTPS is used",
        )
        cmd_parser.add_argument(
            "--proxy-support",
            required=False,
            dest="proxy_support",
            type=str,
            default=None,
            help="ProxySupport value for the export action",
        )
        cmd_parser.add_argument(
            "--proxy-type",
            required=False,
            dest="proxy_type",
            type=str,
            default=None,
            help="ProxyType value for the export action",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the SupportAssist export POST",
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
            "dell-lc-supportassist-export",
            "command export the last Dell SupportAssist collection",
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
        """Append a non-empty candidate URI once.

        :param items: list being built.
        :param value: candidate URI.
        """
        if value and value not in items:
            items.append(value)

    def _candidate_lc_uris(self, do_async):
        """Return candidate DellLCService URIs in discovery-first order.

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
            links = manager.get("Links") if isinstance(manager, dict) else {}
            oem = links.get("Oem") if isinstance(links, dict) else {}
            dell_links = oem.get("Dell") if isinstance(oem, dict) else {}
            if isinstance(dell_links, dict):
                self._append_unique(
                    candidates, self._link(dell_links, "DellLCService")
                )
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
            candidates,
            "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService",
        )
        return candidates

    @staticmethod
    def _allowed_args(action):
        """Return advertised allowable values for a discovered action.

        :param action: discovered RedfishAction.
        :return: dict of argument name to sorted allowable values.
        """
        args = getattr(action, "args", None) or {}
        return {key: sorted(values or []) for key, values in sorted(args.items())}

    def _service_metadata(self, do_async):
        """Discover DellLCService SupportAssist export metadata.

        :param do_async: issue service queries through the async path when True.
        :return: CommandResult containing target metadata or an error.
        """
        checked = []
        for service_uri in self._candidate_lc_uris(do_async):
            service = self._get(service_uri, do_async)
            if not service:
                checked.append(service_uri)
                continue
            actions = self.discover_redfish_actions(self, service)
            target = self._flatten_action_targets(service).get(
                _SUPPORTASSIST_EXPORT_ACTION
            )
            if target:
                action = actions.get("SupportAssistExportLastCollection")
                return CommandResult(
                    {
                        "lc_service": service_uri,
                        "action": _SUPPORTASSIST_EXPORT_ACTION,
                        "target": target,
                        "allowed": self._allowed_args(action),
                    },
                    actions,
                    None,
                    None,
                )
            checked.append(service_uri)
        return CommandResult(
            {
                "action": _SUPPORTASSIST_EXPORT_ACTION,
                "checked": checked,
            },
            None,
            None,
            f"action '{_SUPPORTASSIST_EXPORT_ACTION}' not found",
        )

    @staticmethod
    def _optional_payload_value(value):
        """Normalize optional payload values and drop empty strings.

        :param value: optional action payload value.
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
                raise InvalidArgument(
                    "password environment variable name cannot be empty"
                )
            if env_name not in os.environ:
                raise InvalidArgument(
                    f"password environment variable '{env_name}' is not set"
                )
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
        """Build the SupportAssist export action payload.

        :param kwargs: normalized command arguments.
        :return: JSON-serializable payload with empty values removed.
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
        }
        payload = {
            key: DellLcSupportAssistExport._optional_payload_value(value)
            for key, value in fields.items()
        }
        return {key: value for key, value in payload.items() if value is not None}

    @staticmethod
    def _has_export_request(**kwargs):
        """Return True when invocation options request an action preview/POST.

        :param kwargs: normalized execute arguments.
        :return: True when the command should resolve and invoke the action.
        """
        if kwargs.get("confirm") or kwargs.get("dry_run"):
            return True
        payload_keys = (
            "share_type",
            "ip_address",
            "share_name",
            "file_name",
            "share_username",
            "share_password_env",
            "share_password_file",
            "workgroup",
            "ignore_cert_warning",
            "proxy_support",
            "proxy_type",
        )
        return any(kwargs.get(key) is not None for key in payload_keys)

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
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or run SupportAssistExportLastCollection.

        :param share_type: optional ShareType payload value.
        :param ip_address: optional IPAddress payload value.
        :param share_name: optional ShareName payload value.
        :param file_name: optional FileName payload value.
        :param share_username: optional UserName payload value.
        :param share_password_env: environment variable holding an optional password.
        :param share_password_file: file holding an optional password.
        :param workgroup: optional Workgroup payload value.
        :param ignore_cert_warning: optional IgnoreCertWarning payload value.
        :param proxy_support: optional ProxySupport payload value.
        :param proxy_type: optional ProxyType payload value.
        :param confirm: authorize the export POST to actually fire.
        :param dry_run: resolve the target and show the payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying queries/POST through the async path.
        :return: CommandResult with target metadata, preview, or POST result.
        """
        options = {
            "share_type": share_type,
            "ip_address": ip_address,
            "share_name": share_name,
            "file_name": file_name,
            "share_username": share_username,
            "share_password_env": share_password_env,
            "share_password_file": share_password_file,
            "workgroup": workgroup,
            "ignore_cert_warning": ignore_cert_warning,
            "proxy_support": proxy_support,
            "proxy_type": proxy_type,
            "confirm": confirm,
            "dry_run": dry_run,
        }
        metadata = self._service_metadata(bool(do_async))
        if metadata.error or not self._has_export_request(**options):
            return metadata

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
        )
        result = self.invoke_action(
            metadata.data["lc_service"],
            "SupportAssistExportLastCollection",
            payload=payload,
            full_action_type=_SUPPORTASSIST_EXPORT_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        return self._redact_password(result)
