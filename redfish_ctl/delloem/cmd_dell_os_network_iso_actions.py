"""Preview or invoke Dell OS deployment network ISO actions.

    redfish_ctl dell-os-network-iso-actions
    redfish_ctl dell-os-network-iso-actions --action configurable-boot-network-iso
    redfish_ctl dell-os-network-iso-actions --action download-iso-to-vflash \
        --share-name /isos --image-name ubuntu.iso --confirm

The command discovers ``DellOSDeploymentService`` from ComputerSystem OEM links
or the standard per-system OEM path. Selected actions preview by default, redact
credential-like payload fields in returned previews, and only POST when
``--confirm`` is supplied.

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


@dataclass(frozen=True)
class _OsNetworkIsoAction:
    """Selector metadata for one Dell OS deployment network ISO action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "configurable-boot-network-iso": _OsNetworkIsoAction(
        selector="configurable-boot-network-iso",
        full_type="#DellOSDeploymentService.ConfigurableBootToNetworkISO",
        action_name="ConfigurableBootToNetworkISO",
        description="boot from a network ISO with an explicit reset mode",
    ),
    "download-iso-to-vflash": _OsNetworkIsoAction(
        selector="download-iso-to-vflash",
        full_type="#DellOSDeploymentService.DownloadISOToVFlash",
        action_name="DownloadISOToVFlash",
        description="download a network ISO image to VFlash",
    ),
    "unpack-and-attach": _OsNetworkIsoAction(
        selector="unpack-and-attach",
        full_type="#DellOSDeploymentService.UnpackAndAttach",
        action_name="UnpackAndAttach",
        description="unpack the staged ISO and attach its driver image",
    ),
    "unpack-and-share": _OsNetworkIsoAction(
        selector="unpack-and-share",
        full_type="#DellOSDeploymentService.UnpackAndShare",
        action_name="UnpackAndShare",
        description="unpack a staged ISO and publish it through a share",
    ),
}


class DellOsNetworkIsoActions(
    RedfishManagerBase,
    scm_type=ApiRequestType.DellOsNetworkIsoActions,
    name="dell-os-network-iso-actions",
    metaclass=Singleton,
):
    """Discover and invoke Dell OS deployment network ISO actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-os-network-iso-actions command."""
        super(DellOsNetworkIsoActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-os-network-iso-actions`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="OS deployment action to preview or invoke; omit to list targets",
        )
        cmd_parser.add_argument(
            "--system",
            default=None,
            help="ComputerSystem Id or URI when multiple systems expose the service",
        )
        cmd_parser.add_argument(
            "--service-uri",
            dest="service_uri",
            default=None,
            help="specific DellOSDeploymentService URI to target",
        )
        cmd_parser.add_argument(
            "--payload-json",
            dest="payload_json",
            default=None,
            help="JSON object payload for vendor-specific network ISO fields",
        )
        cmd_parser.add_argument(
            "--ip-addr",
            "--ip_addr",
            dest="ip_addr",
            default=None,
            help="network share host address",
        )
        cmd_parser.add_argument(
            "--share-type",
            "--share_type",
            dest="share_type",
            default=None,
            help="ShareType value advertised by the BMC, such as CIFS, NFS, or TFTP",
        )
        cmd_parser.add_argument(
            "--share-name",
            "--share_name",
            dest="share_name",
            default=None,
            help="network share name or export path",
        )
        cmd_parser.add_argument(
            "--image-name",
            "--remote-image",
            "--remote_image",
            dest="image_name",
            default=None,
            help="ISO image file name on the network share",
        )
        cmd_parser.add_argument(
            "--username",
            "--remote-username",
            "--remote_username",
            dest="share_username",
            default=None,
            help="optional network share username",
        )
        password_group = cmd_parser.add_mutually_exclusive_group(required=False)
        password_group.add_argument(
            "--password-env",
            dest="share_password_env",
            default=None,
            help="environment variable containing the network share password",
        )
        password_group.add_argument(
            "--password-file",
            dest="share_password_file",
            default=None,
            help="file containing the network share password",
        )
        cmd_parser.add_argument(
            "--workgroup",
            "--remote-workgroup",
            "--remote_workgroup",
            dest="workgroup",
            default=None,
            help="optional CIFS workgroup",
        )
        cmd_parser.add_argument(
            "--hash-type",
            dest="hash_type",
            default=None,
            help="HashType value advertised by the BMC, such as MD5 or SHA1",
        )
        cmd_parser.add_argument(
            "--image-hash-value",
            dest="image_hash_value",
            default=None,
            help="expected image hash value when HashType is supplied",
        )
        cmd_parser.add_argument(
            "--reset-type",
            dest="reset_type",
            default=None,
            help="ResetType value for ConfigurableBootToNetworkISO",
        )
        cmd_parser.add_argument(
            "--expose-duration",
            dest="expose_duration",
            default=None,
            help="optional Dell expose-duration payload value",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the selected action instead of previewing it",
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
            "dell-os-network-iso-actions",
            "command run Dell OS deployment network ISO actions",
        )

    @staticmethod
    def _members(data):
        """Return collection member URIs from a Redfish collection.

        :param data: Redfish collection body.
        :return: list of member ``@odata.id`` strings.
        """
        if not isinstance(data, dict):
            return []
        return [
            member["@odata.id"]
            for member in data.get("Members", [])
            if isinstance(member, dict)
            and isinstance(member.get("@odata.id"), str)
        ]

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` from a linked Redfish property.

        :param data: resource body that may carry the link.
        :param key: property name to inspect.
        :return: linked URI, or None when absent.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _nested_link(data, *keys):
        """Follow nested dict keys and return the final ``@odata.id``.

        :param data: resource body to walk.
        :param keys: nested dict keys to follow.
        :return: linked URI, or None when any step is absent.
        """
        value = data
        for key in keys:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return DellOsNetworkIsoActions._link({"value": value}, "value")

    @staticmethod
    def _resource_id(uri):
        """Return the trailing Redfish URI segment.

        :param uri: Redfish resource URI.
        :return: trailing URI segment.
        """
        return uri.rstrip("/").rsplit("/", 1)[-1]

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
        """Read an optional network-share password from an env var or file.

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
    def _service_candidates(system_uri, system):
        """Return candidate DellOSDeploymentService URIs for one system.

        :param system_uri: ComputerSystem URI.
        :param system: ComputerSystem resource body.
        :return: ordered list of candidate service URIs.
        """
        linked = DellOsNetworkIsoActions._nested_link(
            system,
            "Links",
            "Oem",
            "Dell",
            "DellOSDeploymentService",
        )
        direct = DellOsNetworkIsoActions._nested_link(
            system,
            "Oem",
            "Dell",
            "DellOSDeploymentService",
        )
        system_id = DellOsNetworkIsoActions._resource_id(system_uri)
        candidates = [
            linked,
            direct,
            f"{system_uri.rstrip('/')}/Oem/Dell/DellOSDeploymentService",
            f"{RedfishApi.Version}/Dell/Systems/{system_id}/DellOSDeploymentService",
        ]
        seen = set()
        result = []
        for uri in candidates:
            if uri and uri not in seen:
                seen.add(uri)
                result.append(uri)
        return result

    def _get(self, uri, do_async, optional=False):
        """GET a Redfish resource body.

        :param uri: Redfish resource URI to fetch.
        :param do_async: issue the query on the async path when True.
        :param optional: treat a failed read as an empty object when True.
        :return: parsed resource body.
        :raises InvalidArgument: when a required read fails or is not an object.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            if optional:
                return {}
            raise InvalidArgument(f"failed to read {uri}: {exc}") from exc
        if not isinstance(data, dict):
            if optional:
                return {}
            raise InvalidArgument(f"unexpected response from {uri}: expected object")
        return data

    def _discover_rows(self, do_async, service_uri=None):
        """Discover DellOSDeploymentService resources and supported actions.

        :param do_async: issue GET requests on the async path when True.
        :param service_uri: optional direct service URI to inspect.
        :return: list of discovered service rows.
        """
        if service_uri:
            service = self._get(service_uri, do_async)
            return [self._row_for_service(service_uri, service_uri, service)]

        systems = self._get(f"{RedfishApi.Version}/Systems", do_async)
        rows = []
        seen_services = set()
        for system_uri in self._members(systems):
            system = self._get(system_uri, do_async, optional=True)
            for candidate_uri in self._service_candidates(system_uri, system):
                if candidate_uri in seen_services:
                    continue
                seen_services.add(candidate_uri)
                service = self._get(candidate_uri, do_async, optional=True)
                row = self._row_for_service(system_uri, candidate_uri, service)
                if row["Actions"]:
                    rows.append(row)
        return rows

    def _row_for_service(self, system_uri, service_uri, service):
        """Build one discovered-service row from an OS deployment resource.

        :param system_uri: owning ComputerSystem URI.
        :param service_uri: DellOSDeploymentService URI.
        :param service: DellOSDeploymentService response body.
        :return: row with matching network ISO action metadata.
        """
        actions = self.discover_redfish_actions(self, service)
        targets = self._flatten_action_targets(service)
        action_rows = []
        for spec in _ACTION_SPECS.values():
            target = targets.get(spec.full_type)
            if not target:
                continue
            action = actions.get(spec.action_name)
            action_rows.append({
                "Action": spec.selector,
                "FullType": spec.full_type,
                "Target": target,
                "Description": spec.description,
                "AllowableValues": getattr(action, "args", None) or {},
            })
        system_id = (
            service.get("SystemId")
            if isinstance(service, dict)
            else None
        )
        return {
            "System": system_id or self._resource_id(system_uri),
            "SystemUri": system_uri,
            "Id": service.get("Id") if isinstance(service, dict) else None,
            "Name": service.get("Name") if isinstance(service, dict) else None,
            "Uri": service_uri,
            "Actions": action_rows,
        }

    @staticmethod
    def _resolve_row(rows, system=None, service_uri=None):
        """Resolve a selected OS deployment service row.

        :param rows: discovered rows from :meth:`_discover_rows`.
        :param system: optional ComputerSystem Id or URI selector.
        :param service_uri: optional DellOSDeploymentService URI selector.
        :return: matching row.
        :raises InvalidArgument: when selection is missing or ambiguous.
        """
        matches = list(rows)
        if service_uri:
            wanted = service_uri.rstrip("/")
            matches = [row for row in rows if row["Uri"].rstrip("/") == wanted]
        elif system:
            wanted = system.rstrip("/")
            folded = wanted.lower()
            matches = [
                row for row in rows
                if row["SystemUri"].rstrip("/") == wanted
                or str(row["System"]).lower() == folded
            ]
        if not matches:
            raise InvalidArgument("DellOSDeploymentService resource not found")
        if len(matches) > 1:
            systems = [row["System"] for row in matches]
            raise InvalidArgument(
                "multiple DellOSDeploymentService resources found; pass --system "
                f"or --service-uri: {systems}"
            )
        return matches[0]

    @classmethod
    def _payload(cls,
                 payload_json=None,
                 ip_addr=None,
                 share_type=None,
                 share_name=None,
                 image_name=None,
                 share_username=None,
                 share_password=None,
                 workgroup=None,
                 hash_type=None,
                 image_hash_value=None,
                 reset_type=None,
                 expose_duration=None):
        """Build an action payload from JSON plus typed CLI fields.

        :param payload_json: JSON object text with vendor-specific fields.
        :param ip_addr: optional IPAddress value.
        :param share_type: optional ShareType value.
        :param share_name: optional ShareName value.
        :param image_name: optional ImageName value.
        :param share_username: optional UserName value.
        :param share_password: optional Password value.
        :param workgroup: optional Workgroup value.
        :param hash_type: optional HashType value.
        :param image_hash_value: optional ImageHashValue value.
        :param reset_type: optional ResetType value.
        :param expose_duration: optional ExposeDuration value.
        :return: JSON-serializable payload dict.
        """
        payload = cls._payload_from_json(payload_json)
        updates = {
            "IPAddress": cls._clean(ip_addr),
            "ShareType": cls._clean(share_type),
            "ShareName": cls._clean(share_name),
            "ImageName": cls._clean(image_name),
            "UserName": cls._clean(share_username),
            "Password": cls._clean(share_password),
            "Workgroup": cls._clean(workgroup),
            "HashType": cls._clean(hash_type),
            "ImageHashValue": cls._clean(image_hash_value),
            "ResetType": cls._clean(reset_type),
            "ExposeDuration": cls._clean(expose_duration),
        }
        payload.update({
            key: value
            for key, value in updates.items()
            if value is not None
        })
        return payload

    @classmethod
    def _redact_result(cls, result):
        """Mask credential-like fields in returned payloads.

        :param result: CommandResult returned by ``invoke_action``.
        :return: CommandResult with any payload credentials redacted.
        """
        if isinstance(result.data, dict) and isinstance(result.data.get("payload"), dict):
            result.data["payload"] = RedfishManagerBase._redact_sensitive_payload(
                result.data["payload"]
            )
        return result

    def execute(self,
                action: Optional[str] = None,
                system: Optional[str] = None,
                service_uri: Optional[str] = None,
                payload_json: Optional[str] = None,
                ip_addr: Optional[str] = None,
                share_type: Optional[str] = None,
                share_name: Optional[str] = None,
                image_name: Optional[str] = None,
                share_username: Optional[str] = None,
                share_password_env: Optional[str] = None,
                share_password_file: Optional[str] = None,
                workgroup: Optional[str] = None,
                hash_type: Optional[str] = None,
                image_hash_value: Optional[str] = None,
                reset_type: Optional[str] = None,
                expose_duration: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or run Dell OS deployment network ISO actions.

        :param action: optional action selector; None lists discovered targets.
        :param system: optional ComputerSystem Id or URI selector.
        :param service_uri: optional DellOSDeploymentService URI selector.
        :param payload_json: JSON object payload for vendor-specific fields.
        :param ip_addr: optional IPAddress value.
        :param share_type: optional ShareType value.
        :param share_name: optional ShareName value.
        :param image_name: optional ImageName value.
        :param share_username: optional UserName value.
        :param share_password_env: environment variable containing a password.
        :param share_password_file: file containing a password.
        :param workgroup: optional Workgroup value.
        :param hash_type: optional HashType value.
        :param image_hash_value: optional ImageHashValue value.
        :param reset_type: optional ResetType value.
        :param expose_duration: optional ExposeDuration value.
        :param confirm: authorize a POST. Without this every selected action previews.
        :param dry_run: force a preview even with ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying Redfish calls on the async path.
        :return: CommandResult with discovered targets, preview, or POST result.
        :raises InvalidArgument: when selection arguments are invalid.
        """
        rows = self._discover_rows(bool(do_async), service_uri=service_uri)
        if action is None:
            return CommandResult({"os_deployment_targets": rows}, None, None, None)

        spec = _ACTION_SPECS[action]
        row = self._resolve_row(rows, system=system, service_uri=service_uri)
        password = self._password_from_source(
            share_password_env,
            share_password_file,
        )
        result = self.invoke_action(
            row["Uri"],
            spec.action_name,
            payload=self._payload(
                payload_json=payload_json,
                ip_addr=ip_addr,
                share_type=share_type,
                share_name=share_name,
                image_name=image_name,
                share_username=share_username,
                share_password=password,
                workgroup=workgroup,
                hash_type=hash_type,
                image_hash_value=image_hash_value,
                reset_type=reset_type,
                expose_duration=expose_duration,
            ),
            full_action_type=spec.full_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        return self._redact_result(result)
