"""Run Dell SoftwareInstallationService install actions.

    redfish_ctl dell-software-update-install
    redfish_ctl dell-software-update-install --action repository --share-name /repo
    redfish_ctl dell-software-update-install --action uri --uri https://repo.example/fw.exe \
        --confirm

The Dell OEM ``DellSoftwareInstallationService`` advertises software install
actions from the ComputerSystem OEM links. These actions can install firmware
or software, so the command discovers and previews the target by default and
only POSTs when ``--confirm`` is supplied.

Author Mus spyroot@gmail.com
"""
import json
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

_SERVICE_FALLBACK = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/"
    "DellSoftwareInstallationService"
)
_REDACTED = "********"


@dataclass(frozen=True)
class _DellSoftwareInstallSpec:
    """Selector metadata for one Dell software-installation action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "repository": _DellSoftwareInstallSpec(
        selector="repository",
        full_type="#DellSoftwareInstallationService.InstallFromRepository",
        action_name="InstallFromRepository",
        description="install updates from a repository share",
    ),
    "uri": _DellSoftwareInstallSpec(
        selector="uri",
        full_type="#DellSoftwareInstallationService.InstallFromURI",
        action_name="InstallFromURI",
        description="install an update from a specific URI",
    ),
}


class DellSoftwareUpdateInstall(
    RedfishManagerBase,
    scm_type=ApiRequestType.DellSoftwareUpdateInstall,
    name="dell-software-update-install",
    metaclass=Singleton,
):
    """Discover and invoke Dell software-installation update actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-software-update-install command."""
        super(DellSoftwareUpdateInstall, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-software-update-install`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="install action to run; omit to list available targets",
        )
        cmd_parser.add_argument(
            "--software-service-uri",
            dest="software_service_uri",
            type=str,
            default=None,
            help="specific DellSoftwareInstallationService URI when discovery needs override",
        )
        cmd_parser.add_argument(
            "--payload-json",
            dest="payload_json",
            type=str,
            default=None,
            help="JSON object payload for vendor-specific install fields",
        )
        cmd_parser.add_argument(
            "--uri",
            dest="install_uri",
            type=str,
            default=None,
            help="InstallFromURI URI value",
        )
        cmd_parser.add_argument(
            "--share-name",
            dest="share_name",
            type=str,
            default=None,
            help="repository share name for InstallFromRepository",
        )
        cmd_parser.add_argument(
            "--share-type",
            dest="share_type",
            type=str,
            default=None,
            help="repository ShareType value advertised by the BMC",
        )
        cmd_parser.add_argument(
            "--catalog-file",
            dest="catalog_file",
            type=str,
            default=None,
            help="optional repository catalog file name",
        )
        cmd_parser.add_argument(
            "--apply-update",
            dest="apply_update",
            type=str,
            default=None,
            help="InstallFromRepository ApplyUpdate value, usually True or False",
        )
        cmd_parser.add_argument(
            "--ignore-cert-warning",
            dest="ignore_cert_warning",
            type=str,
            default=None,
            help="IgnoreCertWarning value advertised by the BMC",
        )
        cmd_parser.add_argument(
            "--proxy-support",
            dest="proxy_support",
            type=str,
            default=None,
            help="ProxySupport value advertised by the BMC",
        )
        cmd_parser.add_argument(
            "--proxy-type",
            dest="proxy_type",
            type=str,
            default=None,
            help="ProxyType value advertised by the BMC",
        )
        cmd_parser.add_argument(
            "--username",
            dest="software_username",
            type=str,
            default=None,
            help="optional repository or URI username",
        )
        password_group = cmd_parser.add_mutually_exclusive_group(required=False)
        password_group.add_argument(
            "--password-env",
            dest="software_password_env",
            type=str,
            default=None,
            help="environment variable containing the repository or URI password",
        )
        password_group.add_argument(
            "--password-file",
            dest="software_password_file",
            type=str,
            default=None,
            help="file containing the repository or URI password",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the install action POST; without it the command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-software-update-install",
            "command run Dell software installation actions",
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
    def _dell(data):
        """Return the ``Oem.Dell`` extension block from a resource.

        :param data: Redfish resource body.
        :return: Dell OEM block, or an empty dict.
        """
        oem = data.get("Oem") if isinstance(data, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    @staticmethod
    def _clean(value):
        """Strip optional string values and omit blank strings.

        :param value: candidate payload value.
        :return: stripped string, original value, or None when blank.
        """
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @staticmethod
    def _payload_from_json(payload_json):
        """Parse a JSON object payload.

        :param payload_json: JSON object text, or None.
        :return: parsed payload dict.
        :raises InvalidArgument: when the JSON is invalid or not an object.
        """
        if payload_json is None:
            return {}
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise InvalidArgument(f"invalid --payload-json: {exc}") from exc
        if not isinstance(payload, dict):
            raise InvalidArgument("--payload-json must be a JSON object")
        return dict(payload)

    @staticmethod
    def _password_from_source(password_env=None, password_file=None):
        """Read an optional repository password from an env var or file.

        :param password_env: name of the environment variable to read.
        :param password_file: path to a file containing the password.
        :return: password string, or None when no source was provided.
        :raises InvalidArgument: when the source cannot be read.
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
    def _is_sensitive_key(key):
        """Return True for payload keys whose values should not be echoed.

        :param key: payload key name.
        :return: True when the key is credential-like.
        """
        lowered = key.lower()
        return (
            "password" in lowered
            or "secret" in lowered
            or "token" in lowered
            or lowered in {"apikey", "api_key", "accesskey", "access_key"}
        )

    @classmethod
    def _redact_payload(cls, payload):
        """Return a copy of a payload with credential-like fields masked.

        :param payload: action payload dict.
        :return: redacted payload copy.
        """
        return {
            key: _REDACTED if cls._is_sensitive_key(key) else value
            for key, value in payload.items()
        }

    @classmethod
    def _redact_result(cls, result):
        """Mask credential-like fields in returned dry-run/error payloads.

        :param result: CommandResult returned by ``invoke_action``.
        :return: CommandResult with any payload credentials redacted.
        """
        if isinstance(result.data, dict) and isinstance(result.data.get("payload"), dict):
            result.data["payload"] = cls._redact_payload(result.data["payload"])
        return result

    @classmethod
    def _payload(cls,
                 action,
                 payload_json=None,
                 install_uri=None,
                 share_name=None,
                 share_type=None,
                 catalog_file=None,
                 apply_update=None,
                 ignore_cert_warning=None,
                 proxy_support=None,
                 proxy_type=None,
                 software_username=None,
                 software_password=None):
        """Build an action payload from JSON plus typed CLI fields.

        :param action: selected install action.
        :param payload_json: JSON object text with vendor-specific fields.
        :param install_uri: optional InstallFromURI URI.
        :param share_name: optional repository share name.
        :param share_type: optional repository ShareType value.
        :param catalog_file: optional repository catalog file.
        :param apply_update: optional ApplyUpdate value.
        :param ignore_cert_warning: optional IgnoreCertWarning value.
        :param proxy_support: optional ProxySupport value.
        :param proxy_type: optional ProxyType value.
        :param software_username: optional username for the repository or URI.
        :param software_password: optional password read from a safe source.
        :return: JSON-serializable payload dict.
        """
        payload = cls._payload_from_json(payload_json)
        updates = {
            "IgnoreCertWarning": cls._clean(ignore_cert_warning),
            "ProxySupport": cls._clean(proxy_support),
            "ProxyType": cls._clean(proxy_type),
            "UserName": cls._clean(software_username),
            "Password": cls._clean(software_password),
        }
        if action == "uri":
            updates["URI"] = cls._clean(install_uri)
        else:
            updates.update({
                "ShareName": cls._clean(share_name),
                "ShareType": cls._clean(share_type),
                "CatalogFile": cls._clean(catalog_file),
                "ApplyUpdate": cls._clean(apply_update),
            })
        payload.update({key: value for key, value in updates.items()
                        if value is not None})
        return payload

    def _get(self, uri, do_async):
        """GET a Redfish resource body, treating optional misses as absent.

        :param uri: Redfish resource URI.
        :param do_async: run the query asynchronously when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _system_uris(self, do_async):
        """Return ComputerSystem member URIs.

        :param do_async: run the query asynchronously when True.
        :return: list of system resource URIs.
        """
        root = self._get(RedfishApi.Version, do_async)
        systems_uri = self._link(root, "Systems") or f"{RedfishApi.Version}/Systems"
        systems = self._get(systems_uri, do_async)
        members = systems.get("Members") if isinstance(systems, dict) else []
        return [
            member["@odata.id"]
            for member in members
            if isinstance(member, dict) and isinstance(member.get("@odata.id"), str)
        ]

    def _service_uris(self, do_async, service_uri=None):
        """Discover DellSoftwareInstallationService resource URIs.

        :param do_async: run the query asynchronously when True.
        :param service_uri: optional caller-supplied service URI.
        :return: ordered list of discovered service URIs.
        """
        if service_uri:
            return [service_uri]

        uris = []
        for system_uri in self._system_uris(do_async):
            system = self._get(system_uri, do_async)
            discovered = self._link(
                self._dell(system),
                "DellSoftwareInstallationService",
            )
            if discovered and discovered not in uris:
                uris.append(discovered)
        if not uris:
            uris.append(_SERVICE_FALLBACK)
        return uris

    def _discover_rows(self, do_async, service_uri=None):
        """Discover available Dell software installation actions.

        :param do_async: run underlying GETs asynchronously when True.
        :param service_uri: optional caller-supplied service URI.
        :return: list of available install-action rows.
        """
        rows = []
        for candidate_uri in self._service_uris(do_async, service_uri):
            service = self._get(candidate_uri, do_async)
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
                    "Resource": candidate_uri,
                    "Target": target,
                    "Description": spec.description,
                    "AllowableValues": getattr(action, "args", None) or {},
                })
        return rows

    def execute(self,
                action: Optional[str] = None,
                software_service_uri: Optional[str] = None,
                payload_json: Optional[str] = None,
                install_uri: Optional[str] = None,
                share_name: Optional[str] = None,
                share_type: Optional[str] = None,
                catalog_file: Optional[str] = None,
                apply_update: Optional[str] = None,
                ignore_cert_warning: Optional[str] = None,
                proxy_support: Optional[str] = None,
                proxy_type: Optional[str] = None,
                software_username: Optional[str] = None,
                software_password_env: Optional[str] = None,
                software_password_file: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or run Dell software update install actions.

        :param action: selector from ``_ACTION_SPECS``; omit to list targets.
        :param software_service_uri: optional direct service resource URI.
        :param payload_json: JSON object payload for vendor-specific fields.
        :param install_uri: optional InstallFromURI URI value.
        :param share_name: optional repository share name.
        :param share_type: optional repository ShareType value.
        :param catalog_file: optional repository catalog file.
        :param apply_update: optional repository ApplyUpdate value.
        :param ignore_cert_warning: optional IgnoreCertWarning value.
        :param proxy_support: optional ProxySupport value.
        :param proxy_type: optional ProxyType value.
        :param software_username: optional username for the repository or URI.
        :param software_password_env: environment variable containing a password.
        :param software_password_file: file containing a password.
        :param confirm: authorize the install action POST to actually fire.
        :param dry_run: resolve the target and show the payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        :raises InvalidArgument: when an action has no payload fields.
        """
        rows = self._discover_rows(bool(do_async), software_service_uri)
        if action is None:
            return CommandResult(rows, None, None, None)

        matches = [row for row in rows if row["Action"] == action]
        if not matches:
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"Dell software update install action not found: {action}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                f"multiple Dell software update install targets found: {action}",
            )

        spec = _ACTION_SPECS[action]
        payload = self._payload(
            action,
            payload_json=payload_json,
            install_uri=install_uri,
            share_name=share_name,
            share_type=share_type,
            catalog_file=catalog_file,
            apply_update=apply_update,
            ignore_cert_warning=ignore_cert_warning,
            proxy_support=proxy_support,
            proxy_type=proxy_type,
            software_username=software_username,
            software_password=self._password_from_source(
                software_password_env,
                software_password_file,
            ),
        )
        if not payload:
            raise InvalidArgument(
                f"--action {action} requires --payload-json or an install option"
            )

        result = self.invoke_action(
            matches[0]["Resource"],
            spec.action_name,
            payload=payload,
            full_action_type=spec.full_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        return self._redact_result(result)
