"""Preview or invoke Dell OEM license-management actions.

    redfish_ctl dell-license-actions
    redfish_ctl dell-license-actions --action export-to-share --share-type NFS \
        --share-address 192.0.2.10
    redfish_ctl dell-license-actions --action delete --entitlement-id LIC-1 --confirm

The command resolves Dell's ``DellLicenseManagementService`` from Manager OEM
links, lists advertised action targets by default, and previews selected actions
unless ``--confirm`` is passed. License material and share passwords are read
from environment variables or files and masked in returned preview payloads.

Author Mus spyroot@gmail.com
"""
import os
from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..actions.action_policy import classify
from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_LICENSE_MANAGEMENT_FALLBACK = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DellLicenseManagementService"
)
_REDACTED = "********"


@dataclass(frozen=True)
class _LicenseActionSpec:
    """Selector metadata for one Dell license-management action."""

    selector: str
    full_type: str
    action_name: str
    description: str
    requires_device: bool = False
    requires_network_share: bool = False
    requires_license_data: bool = False


_ACTION_SPECS = {
    "delete": _LicenseActionSpec(
        selector="delete",
        full_type="#DellLicenseManagementService.DeleteLicense",
        action_name="DeleteLicense",
        description="delete one or more installed Dell licenses",
    ),
    "export": _LicenseActionSpec(
        selector="export",
        full_type="#DellLicenseManagementService.ExportLicense",
        action_name="ExportLicense",
        description="export installed license data in the response",
    ),
    "export-by-device": _LicenseActionSpec(
        selector="export-by-device",
        full_type="#DellLicenseManagementService.ExportLicenseByDevice",
        action_name="ExportLicenseByDevice",
        description="export license data for a selected device",
        requires_device=True,
    ),
    "export-by-device-to-share": _LicenseActionSpec(
        selector="export-by-device-to-share",
        full_type="#DellLicenseManagementService.ExportLicenseByDeviceToNetworkShare",
        action_name="ExportLicenseByDeviceToNetworkShare",
        description="export device license data to a network share",
        requires_device=True,
        requires_network_share=True,
    ),
    "export-to-share": _LicenseActionSpec(
        selector="export-to-share",
        full_type="#DellLicenseManagementService.ExportLicenseToNetworkShare",
        action_name="ExportLicenseToNetworkShare",
        description="export installed license data to a network share",
        requires_network_share=True,
    ),
    "import": _LicenseActionSpec(
        selector="import",
        full_type="#DellLicenseManagementService.ImportLicense",
        action_name="ImportLicense",
        description="import license content from an environment variable or file",
        requires_license_data=True,
    ),
    "import-from-share": _LicenseActionSpec(
        selector="import-from-share",
        full_type="#DellLicenseManagementService.ImportLicenseFromNetworkShare",
        action_name="ImportLicenseFromNetworkShare",
        description="import a license from a network share",
        requires_network_share=True,
    ),
}


class DellLicenseActions(RedfishManagerBase,
                         scm_type=ApiRequestType.DellLicenseActions,
                         name="dell-license-actions",
                         metaclass=Singleton):
    """Discover and invoke Dell OEM license-management actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-license-actions command."""
        super(DellLicenseActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-license-actions`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="Dell license action to preview or invoke; omit to list targets",
        )
        cmd_parser.add_argument(
            "--entitlement-id",
            dest="entitlement_id",
            default=None,
            help="license entitlement identifier for delete/export selectors",
        )
        cmd_parser.add_argument(
            "--device",
            default=None,
            help="device identifier for by-device license export selectors",
        )
        cmd_parser.add_argument(
            "--delete-option",
            dest="delete_option",
            default=None,
            help="DeleteOptions payload value advertised by Dell",
        )
        cmd_parser.add_argument(
            "--import-option",
            dest="import_option",
            default=None,
            help="ImportOptions payload value advertised by Dell",
        )
        cmd_parser.add_argument(
            "--share-type",
            dest="share_type",
            default=None,
            help="network share type, such as CIFS, NFS, HTTP, or HTTPS",
        )
        cmd_parser.add_argument(
            "--share-address",
            dest="share_address",
            default=None,
            help="network share host, address, or URI for Dell IPAddress",
        )
        cmd_parser.add_argument(
            "--share-name",
            dest="share_name",
            default=None,
            help="network share name or path",
        )
        cmd_parser.add_argument(
            "--file-name",
            dest="file_name",
            default=None,
            help="license file name on the network share",
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
            "--ignore-cert-warning",
            dest="ignore_cert_warning",
            default=None,
            help="IgnoreCertWarning payload value, usually Off or On",
        )
        cmd_parser.add_argument(
            "--proxy-support",
            dest="proxy_support",
            default=None,
            help="ProxySupport payload value advertised by Dell",
        )
        cmd_parser.add_argument(
            "--proxy-type",
            dest="proxy_type",
            default=None,
            help="ProxyType payload value advertised by Dell",
        )
        cmd_parser.add_argument(
            "--proxy-server",
            dest="proxy_server",
            default=None,
            help="optional proxy server for network-share actions",
        )
        cmd_parser.add_argument(
            "--proxy-port",
            dest="proxy_port",
            type=int,
            default=None,
            help="optional proxy port for network-share actions",
        )
        cmd_parser.add_argument(
            "--proxy-username",
            dest="proxy_username",
            default=None,
            help="optional proxy username",
        )
        proxy_password = cmd_parser.add_mutually_exclusive_group(required=False)
        proxy_password.add_argument(
            "--proxy-password-env",
            dest="proxy_password_env",
            default=None,
            help="environment variable containing the proxy password",
        )
        proxy_password.add_argument(
            "--proxy-password-file",
            dest="proxy_password_file",
            default=None,
            help="file containing the proxy password",
        )
        license_data = cmd_parser.add_mutually_exclusive_group(required=False)
        license_data.add_argument(
            "--license-data-env",
            dest="license_data_env",
            default=None,
            help="environment variable containing direct ImportLicense data",
        )
        license_data.add_argument(
            "--license-data-file",
            dest="license_data_file",
            default=None,
            help="file containing direct ImportLicense data",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the selected license action instead of previewing it",
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
            "dell-license-actions",
            "command manage Dell OEM license actions",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property.

        :param data: resource body containing the link.
        :param key: property name whose ``@odata.id`` to extract.
        :return: linked URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @classmethod
    def _dell_oem_link(cls, manager, key):
        """Return an OEM Dell link from a Manager resource.

        :param manager: Manager resource body.
        :param key: OEM Dell link name.
        :return: the linked URI, or None when absent.
        """
        links = (manager or {}).get("Links", {})
        if not isinstance(links, dict):
            return None
        oem_links = links.get("Oem", {})
        if not isinstance(oem_links, dict):
            return None
        dell_links = oem_links.get("Dell", {})
        if not isinstance(dell_links, dict):
            return None
        return cls._link(dell_links, key)

    def _manager_link(self, key, do_async):
        """Return a Dell OEM link from the first Manager that advertises it.

        :param key: OEM Dell link name to resolve.
        :param do_async: issue Manager queries over the async Redfish path.
        :return: linked URI, or None when no Manager advertises it.
        """
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            try:
                manager = self.base_query(
                    manager_uri.rstrip("/"),
                    do_async=do_async,
                ).data or {}
            except Exception:
                continue
            target = self._dell_oem_link(manager, key)
            if target:
                return target
        return None

    def _license_management_uri(self, do_async):
        """Resolve DellLicenseManagementService from Manager OEM links.

        :param do_async: issue Manager queries over the async Redfish path.
        :return: discovered license-management service URI, or the Dell fallback.
        """
        return (
            self._manager_link("DellLicenseManagementService", do_async)
            or _LICENSE_MANAGEMENT_FALLBACK
        )

    def _license_management_service(self, do_async):
        """Read the DellLicenseManagementService resource.

        :param do_async: issue the query over the async Redfish path.
        :return: tuple of ``(uri, body)`` or ``(uri, CommandResult)`` on error.
        """
        uri = self._license_management_uri(do_async)
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            return uri, CommandResult(None, None, None, f"failed to read {uri}: {exc}")
        if not isinstance(data, dict):
            return uri, CommandResult(
                None,
                None,
                None,
                f"unexpected response from {uri}: expected object",
            )
        return uri, data

    @staticmethod
    def _action_details(actions, targets):
        """Return sorted metadata for advertised license-management actions.

        :param actions: discovered short-name action map.
        :param targets: full action type to target URI map.
        :return: list of JSON-serializable action metadata dictionaries.
        """
        selectors = {
            spec.full_type: selector
            for selector, spec in _ACTION_SPECS.items()
        }
        rows = []
        for full_action, target in sorted(targets.items()):
            short = full_action.rsplit(".", 1)[-1].lstrip("#")
            action = actions.get(short)
            args = getattr(action, "args", None) or {}
            spec = _ACTION_SPECS.get(selectors.get(full_action))
            rows.append({
                "selector": selectors.get(full_action),
                "name": short,
                "action": full_action,
                "target": target,
                "description": getattr(spec, "description", None),
                "level": classify(full_action).value,
                "supported": full_action in selectors,
                "parameters": {
                    key: sorted(values or [])
                    for key, values in sorted(args.items())
                },
            })
        return rows

    def _license_action_metadata(self, do_async):
        """Return discovered Dell license-management action metadata.

        :param do_async: issue the license-management query over the async path.
        :return: CommandResult with target metadata or an error if absent.
        """
        uri, service = self._license_management_service(do_async)
        if isinstance(service, CommandResult):
            return service
        actions = self.discover_redfish_actions(self, service)
        targets = self._flatten_action_targets(service)
        selectors = {
            selector: {
                "action": spec.full_type,
                "target": targets.get(spec.full_type),
                "available": spec.full_type in targets,
                "level": classify(spec.full_type).value,
            }
            for selector, spec in sorted(_ACTION_SPECS.items())
        }
        return CommandResult(
            {
                "license_management_service": uri,
                "selectors": selectors,
                "actions": self._action_details(actions, targets),
            },
            actions,
            None,
            None,
        )

    @staticmethod
    def _optional_payload_value(value):
        """Normalize optional payload string values.

        :param value: raw CLI or programmatic value.
        :return: stripped value, or None when empty.
        """
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        return value

    @staticmethod
    def _require_payload_value(name, value):
        """Return a non-empty payload value or raise a clear argument error.

        :param name: human-readable argument name.
        :param value: raw CLI or programmatic value.
        :return: stripped value.
        :raises InvalidArgument: when the value is empty.
        """
        normalized = DellLicenseActions._optional_payload_value(value)
        if normalized is None:
            raise InvalidArgument(f"{name} cannot be empty")
        return normalized

    @staticmethod
    def _secret_from_source(kind, env_name=None, file_name=None):
        """Read secret-like payload material from an environment variable or file.

        :param kind: human-readable source kind for error messages.
        :param env_name: optional environment variable name.
        :param file_name: optional file path.
        :return: secret value, or None when neither source is supplied.
        :raises InvalidArgument: when the selected source is absent or unreadable.
        """
        if env_name and file_name:
            raise InvalidArgument(f"use only one {kind} source")
        if env_name is not None:
            name = env_name.strip()
            if not name:
                raise InvalidArgument(
                    f"{kind} environment variable name cannot be empty"
                )
            if name not in os.environ:
                raise InvalidArgument(
                    f"{kind} environment variable '{name}' is not set"
                )
            return os.environ[name]
        if file_name is not None:
            path = Path(file_name).expanduser()
            try:
                return path.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as exc:
                raise InvalidArgument(
                    f"failed to read {kind} file '{path}': {exc}"
                ) from exc
        return None

    @staticmethod
    def _add_optional(payload, key, value):
        """Add a non-empty optional value to a payload.

        :param payload: payload dictionary being built.
        :param key: Redfish payload key.
        :param value: raw value to normalize and add.
        :return: None.
        """
        normalized = DellLicenseActions._optional_payload_value(value)
        if normalized is not None:
            payload[key] = normalized

    @staticmethod
    def _has_network_payload(payload):
        """Return True when payload carries any network share details.

        :param payload: action payload.
        :return: True when a share-related field is present.
        """
        network_keys = {
            "ShareType",
            "IPAddress",
            "ShareName",
            "FileName",
            "UserName",
            "Password",
            "IgnoreCertWarning",
            "ProxySupport",
            "ProxyType",
            "ProxyServer",
            "ProxyPort",
            "ProxyUserName",
            "ProxyPassword",
        }
        return bool(network_keys.intersection(payload))

    @staticmethod
    def _validate_payload_shape(spec, payload):
        """Validate required action-specific payload shape before POST preview.

        :param spec: selected action metadata.
        :param payload: normalized payload.
        :return: None.
        :raises InvalidArgument: when required selector data is missing.
        """
        if spec.selector == "delete" and not (
            payload.get("EntitlementID") or payload.get("DeleteOptions")
        ):
            raise InvalidArgument(
                "--action delete requires --entitlement-id or --delete-option"
            )
        if spec.requires_device and not payload.get("Device"):
            raise InvalidArgument(f"--action {spec.selector} requires --device")
        if spec.requires_network_share:
            missing = [
                flag
                for flag, key in (
                    ("--share-type", "ShareType"),
                    ("--share-address", "IPAddress"),
                )
                if not payload.get(key)
            ]
            if missing:
                raise InvalidArgument(
                    f"--action {spec.selector} requires {', '.join(missing)}"
                )
        elif DellLicenseActions._has_network_payload(payload):
            raise InvalidArgument(
                "network share options are only valid with network-share actions"
            )
        if spec.requires_license_data and not payload.get("LicenseFile"):
            raise InvalidArgument(
                "--action import requires --license-data-env or --license-data-file"
            )
        if not spec.requires_license_data and payload.get("LicenseFile"):
            raise InvalidArgument("license data is only valid with --action import")
        if spec.selector != "delete" and payload.get("DeleteOptions"):
            raise InvalidArgument("--delete-option is only valid with --action delete")
        if (
            spec.selector not in {"import", "import-from-share"}
            and payload.get("ImportOptions")
        ):
            raise InvalidArgument(
                "--import-option is only valid with import license actions"
            )

    @staticmethod
    def _payload(spec,
                 entitlement_id=None,
                 device=None,
                 delete_option=None,
                 import_option=None,
                 share_type=None,
                 share_address=None,
                 share_name=None,
                 file_name=None,
                 share_username=None,
                 share_password_env=None,
                 share_password_file=None,
                 ignore_cert_warning=None,
                 proxy_support=None,
                 proxy_type=None,
                 proxy_server=None,
                 proxy_port=None,
                 proxy_username=None,
                 proxy_password_env=None,
                 proxy_password_file=None,
                 license_data_env=None,
                 license_data_file=None):
        """Build a DellLicenseManagementService action payload.

        :param spec: selected action metadata.
        :param entitlement_id: optional license entitlement identifier.
        :param device: optional device identifier for by-device actions.
        :param delete_option: optional DeleteOptions value.
        :param import_option: optional ImportOptions value.
        :param share_type: optional network share type.
        :param share_address: optional network share address.
        :param share_name: optional network share path/name.
        :param file_name: optional license file name on the share.
        :param share_username: optional network share username.
        :param share_password_env: environment variable holding share password.
        :param share_password_file: file holding share password.
        :param ignore_cert_warning: optional IgnoreCertWarning value.
        :param proxy_support: optional ProxySupport value.
        :param proxy_type: optional ProxyType value.
        :param proxy_server: optional proxy server.
        :param proxy_port: optional proxy port.
        :param proxy_username: optional proxy username.
        :param proxy_password_env: environment variable holding proxy password.
        :param proxy_password_file: file holding proxy password.
        :param license_data_env: environment variable holding direct license data.
        :param license_data_file: file holding direct license data.
        :return: normalized Redfish action payload.
        :raises InvalidArgument: when selector-specific requirements are unmet.
        """
        payload = {}
        DellLicenseActions._add_optional(payload, "EntitlementID", entitlement_id)
        DellLicenseActions._add_optional(payload, "Device", device)
        DellLicenseActions._add_optional(payload, "DeleteOptions", delete_option)
        DellLicenseActions._add_optional(payload, "ImportOptions", import_option)
        DellLicenseActions._add_optional(payload, "ShareType", share_type)
        DellLicenseActions._add_optional(payload, "IPAddress", share_address)
        DellLicenseActions._add_optional(payload, "ShareName", share_name)
        DellLicenseActions._add_optional(payload, "FileName", file_name)
        DellLicenseActions._add_optional(payload, "UserName", share_username)
        DellLicenseActions._add_optional(
            payload,
            "Password",
            DellLicenseActions._secret_from_source(
                "share password",
                share_password_env,
                share_password_file,
            ),
        )
        DellLicenseActions._add_optional(
            payload,
            "IgnoreCertWarning",
            ignore_cert_warning,
        )
        DellLicenseActions._add_optional(payload, "ProxySupport", proxy_support)
        DellLicenseActions._add_optional(payload, "ProxyType", proxy_type)
        DellLicenseActions._add_optional(payload, "ProxyServer", proxy_server)
        DellLicenseActions._add_optional(payload, "ProxyPort", proxy_port)
        DellLicenseActions._add_optional(payload, "ProxyUserName", proxy_username)
        DellLicenseActions._add_optional(
            payload,
            "ProxyPassword",
            DellLicenseActions._secret_from_source(
                "proxy password",
                proxy_password_env,
                proxy_password_file,
            ),
        )
        DellLicenseActions._add_optional(
            payload,
            "LicenseFile",
            DellLicenseActions._secret_from_source(
                "license data",
                license_data_env,
                license_data_file,
            ),
        )
        DellLicenseActions._validate_payload_shape(spec, payload)
        return payload

    @staticmethod
    def _redact_payload(result):
        """Mask secret-like fields in returned dry-run payloads.

        :param result: CommandResult from ``invoke_action``.
        :return: CommandResult with preview payload values redacted.
        """
        if not isinstance(result.data, dict):
            return result
        payload = result.data.get("payload")
        if not isinstance(payload, dict):
            return result
        redacted = dict(payload)
        for key in list(redacted):
            folded = key.lower()
            if (
                "password" in folded
                or "secret" in folded
                or "token" in folded
                or key == "LicenseFile"
            ):
                redacted[key] = _REDACTED
        result.data["payload"] = redacted
        return result

    def execute(self,
                action: Optional[str] = None,
                entitlement_id: Optional[str] = None,
                device: Optional[str] = None,
                delete_option: Optional[str] = None,
                import_option: Optional[str] = None,
                share_type: Optional[str] = None,
                share_address: Optional[str] = None,
                share_name: Optional[str] = None,
                file_name: Optional[str] = None,
                share_username: Optional[str] = None,
                share_password_env: Optional[str] = None,
                share_password_file: Optional[str] = None,
                ignore_cert_warning: Optional[str] = None,
                proxy_support: Optional[str] = None,
                proxy_type: Optional[str] = None,
                proxy_server: Optional[str] = None,
                proxy_port: Optional[int] = None,
                proxy_username: Optional[str] = None,
                proxy_password_env: Optional[str] = None,
                proxy_password_file: Optional[str] = None,
                license_data_env: Optional[str] = None,
                license_data_file: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke Dell OEM license-management actions.

        :param action: optional selector from ``_ACTION_SPECS``; omit to list.
        :param entitlement_id: optional license entitlement identifier.
        :param device: optional device identifier for by-device exports.
        :param delete_option: optional DeleteOptions value.
        :param import_option: optional ImportOptions value.
        :param share_type: optional ShareType value.
        :param share_address: optional Dell IPAddress value.
        :param share_name: optional ShareName value.
        :param file_name: optional FileName value.
        :param share_username: optional UserName value.
        :param share_password_env: env var containing network share password.
        :param share_password_file: file containing network share password.
        :param ignore_cert_warning: optional IgnoreCertWarning value.
        :param proxy_support: optional ProxySupport value.
        :param proxy_type: optional ProxyType value.
        :param proxy_server: optional ProxyServer value.
        :param proxy_port: optional ProxyPort value.
        :param proxy_username: optional ProxyUserName value.
        :param proxy_password_env: env var containing proxy password.
        :param proxy_password_file: file containing proxy password.
        :param license_data_env: env var containing direct ImportLicense data.
        :param license_data_file: file containing direct ImportLicense data.
        :param confirm: authorize the selected action POST.
        :param dry_run: force a preview even when ``confirm`` is True.
        :param filename: accepted for CLI compatibility; not used.
        :param data_type: accepted for CLI compatibility; not used.
        :param verbose: accepted for CLI compatibility; not used.
        :param do_async: issue underlying Redfish calls on the async path.
        :return: CommandResult with metadata, preview, execution result, or error.
        :raises InvalidArgument: when payload inputs are invalid.
        """
        if action is None:
            return self._license_action_metadata(bool(do_async))
        if action not in _ACTION_SPECS:
            allowed = ", ".join(sorted(_ACTION_SPECS))
            raise InvalidArgument(
                f"unsupported Dell license action '{action}'; allowed: {allowed}")
        spec = _ACTION_SPECS[action]
        service_uri, service = self._license_management_service(bool(do_async))
        if isinstance(service, CommandResult):
            return service
        targets = self._flatten_action_targets(service)
        if spec.full_type not in targets:
            return CommandResult(
                {
                    "action": spec.full_type,
                    "available": sorted(targets),
                },
                None,
                None,
                f"action '{spec.full_type}' not found on {service_uri}",
            )
        result = self.invoke_action(
            service_uri,
            spec.action_name,
            payload=self._payload(
                spec,
                entitlement_id=entitlement_id,
                device=device,
                delete_option=delete_option,
                import_option=import_option,
                share_type=share_type,
                share_address=share_address,
                share_name=share_name,
                file_name=file_name,
                share_username=share_username,
                share_password_env=share_password_env,
                share_password_file=share_password_file,
                ignore_cert_warning=ignore_cert_warning,
                proxy_support=proxy_support,
                proxy_type=proxy_type,
                proxy_server=proxy_server,
                proxy_port=proxy_port,
                proxy_username=proxy_username,
                proxy_password_env=proxy_password_env,
                proxy_password_file=proxy_password_file,
                license_data_env=license_data_env,
                license_data_file=license_data_file,
            ),
            full_action_type=spec.full_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        return self._redact_payload(result)
