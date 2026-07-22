"""Preview or invoke DellPersistentStorageService partition actions.

    redfish_ctl dell-vflash-partition
    redfish_ctl dell-vflash-partition --action attach --partition-index 1
    redfish_ctl dell-vflash-partition --action delete --partition-index 1 --confirm
        --i-understand-irreversible

The command resolves Dell's PersistentStorageService from Manager OEM links and
uses the advertised action targets from the resource body. Mutating actions
preview by default; erase-class actions require both confirmation flags.
"""
import os
from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..actions.action_policy import classify
from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_SERVICE_FALLBACK = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DellPersistentStorageService"
)


@dataclass(frozen=True)
class _PartitionActionSpec:
    """Static selector metadata for one Dell persistent-storage action."""

    selector: str
    full_type: str
    action_name: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    description: str


_ACTION_SPECS = {
    "attach": _PartitionActionSpec(
        selector="attach",
        full_type="#DellPersistentStorageService.AttachPartition",
        action_name="AttachPartition",
        required=("PartitionIndex",),
        optional=(),
        description="attach an existing VFlash partition",
    ),
    "create": _PartitionActionSpec(
        selector="create",
        full_type="#DellPersistentStorageService.CreatePartition",
        action_name="CreatePartition",
        required=(
            "PartitionIndex",
            "PartitionType",
            "Size",
            "SizeUnit",
            "OSVolumeLabel",
        ),
        optional=(),
        description="create a blank VFlash partition",
    ),
    "create-from-image": _PartitionActionSpec(
        selector="create-from-image",
        full_type="#DellPersistentStorageService.CreatePartitionUsingImage",
        action_name="CreatePartitionUsingImage",
        required=(
            "PartitionIndex",
            "PartitionType",
            "OSVolumeLabel",
            "ShareType",
        ),
        optional=(
            "IPAddress",
            "ImageName",
            "SharePath",
            "URI",
            "HashType",
            "HashValue",
            "Username",
            "Password",
            "Port",
            "Workgroup",
        ),
        description="create a VFlash partition from an image on a share or URI",
    ),
    "delete": _PartitionActionSpec(
        selector="delete",
        full_type="#DellPersistentStorageService.DeletePartition",
        action_name="DeletePartition",
        required=("PartitionIndex",),
        optional=(),
        description="delete a VFlash partition",
    ),
    "detach": _PartitionActionSpec(
        selector="detach",
        full_type="#DellPersistentStorageService.DetachPartition",
        action_name="DetachPartition",
        required=("PartitionIndex",),
        optional=(),
        description="detach a VFlash partition",
    ),
    "export": _PartitionActionSpec(
        selector="export",
        full_type="#DellPersistentStorageService.ExportDataFromPartition",
        action_name="ExportDataFromPartition",
        required=("PartitionIndex", "ShareType", "ImageName"),
        optional=(
            "IPAddress",
            "SharePath",
            "Username",
            "Password",
            "Port",
            "Workgroup",
        ),
        description="export VFlash partition data to a remote share",
    ),
    "format": _PartitionActionSpec(
        selector="format",
        full_type="#DellPersistentStorageService.FormatPartition",
        action_name="FormatPartition",
        required=("PartitionIndex", "FormatType"),
        optional=(),
        description="format a VFlash partition",
    ),
    "initialize": _PartitionActionSpec(
        selector="initialize",
        full_type="#DellPersistentStorageService.InitializeMedia",
        action_name="InitializeMedia",
        required=(),
        optional=(),
        description="initialize VFlash media",
    ),
    "modify": _PartitionActionSpec(
        selector="modify",
        full_type="#DellPersistentStorageService.ModifyPartition",
        action_name="ModifyPartition",
        required=("PartitionIndex", "AccessType"),
        optional=(),
        description="change a VFlash partition access mode",
    ),
}

_PAYLOAD_SOURCES = {
    "PartitionIndex": "partition_index",
    "PartitionType": "partition_type",
    "Size": "size",
    "SizeUnit": "size_unit",
    "OSVolumeLabel": "os_volume_label",
    "FormatType": "format_type",
    "AccessType": "access_type",
    "ShareType": "share_type",
    "IPAddress": "ip_address",
    "ImageName": "image_name",
    "SharePath": "share_path",
    "URI": "uri",
    "HashType": "hash_type",
    "HashValue": "hash_value",
    "Username": "share_username",
    "Password": "password",
    "Port": "share_port",
    "Workgroup": "workgroup",
}

_FLAG_NAMES = {
    "PartitionIndex": "--partition-index",
    "PartitionType": "--partition-type",
    "Size": "--size",
    "SizeUnit": "--size-unit",
    "OSVolumeLabel": "--os-volume-label",
    "FormatType": "--format-type",
    "AccessType": "--access-type",
    "ShareType": "--share-type",
    "IPAddress": "--ip-address",
    "ImageName": "--image-name",
    "SharePath": "--share-path",
    "URI": "--uri",
    "HashType": "--hash-type",
    "HashValue": "--hash-value",
    "Username": "--username",
    "Password": "--password-env or --password-file",
    "Port": "--port",
    "Workgroup": "--workgroup",
}


class DellPersistentPartitionActions(IDracManager,
                                     scm_type=ApiRequestType.DellPersistentPartitionActions,
                                     name="dell-vflash-partition",
                                     metaclass=Singleton):
    """Discover and invoke Dell VFlash partition actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-vflash-partition command."""
        super(DellPersistentPartitionActions, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-vflash-partition`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="partition action to preview or run; omit to list available targets",
        )
        cmd_parser.add_argument(
            "--partition-index",
            dest="partition_index",
            type=int,
            default=None,
            help="Dell VFlash partition index, in the range 1-16",
        )
        cmd_parser.add_argument(
            "--partition-type",
            dest="partition_type",
            type=str,
            default=None,
            help="partition type for create operations",
        )
        cmd_parser.add_argument(
            "--size",
            dest="size",
            type=int,
            default=None,
            help="new blank partition size",
        )
        cmd_parser.add_argument(
            "--size-unit",
            dest="size_unit",
            type=str,
            default=None,
            help="unit for --size, such as MB or GB",
        )
        cmd_parser.add_argument(
            "--os-volume-label",
            dest="os_volume_label",
            type=str,
            default=None,
            help="OS volume label used when creating a partition",
        )
        cmd_parser.add_argument(
            "--format-type",
            dest="format_type",
            type=str,
            default=None,
            help="filesystem type for format, such as FAT32",
        )
        cmd_parser.add_argument(
            "--access-type",
            dest="access_type",
            type=str,
            default=None,
            help="access mode for modify, such as Read-Only or Read-Write",
        )
        cmd_parser.add_argument(
            "--share-type",
            dest="share_type",
            type=str,
            default=None,
            help="remote share type for image import/export actions",
        )
        cmd_parser.add_argument(
            "--ip-address",
            dest="ip_address",
            type=str,
            default=None,
            help="remote share IP address or host name",
        )
        cmd_parser.add_argument(
            "--image-name",
            dest="image_name",
            type=str,
            default=None,
            help="remote image file name for import/export actions",
        )
        cmd_parser.add_argument(
            "--share-path",
            dest="share_path",
            type=str,
            default=None,
            help="remote share path for import/export actions",
        )
        cmd_parser.add_argument(
            "--uri",
            dest="uri",
            type=str,
            default=None,
            help="direct image URI for create-from-image",
        )
        cmd_parser.add_argument(
            "--hash-type",
            dest="hash_type",
            type=str,
            default=None,
            help="image hash algorithm for create-from-image",
        )
        cmd_parser.add_argument(
            "--hash-value",
            dest="hash_value",
            type=str,
            default=None,
            help="image hash value for create-from-image",
        )
        cmd_parser.add_argument(
            "--username",
            dest="share_username",
            type=str,
            default=None,
            help="optional remote share username",
        )
        password_group = cmd_parser.add_mutually_exclusive_group(required=False)
        password_group.add_argument(
            "--password-env",
            dest="password_env",
            type=str,
            default=None,
            help="environment variable containing the remote share password",
        )
        password_group.add_argument(
            "--password-file",
            dest="password_file",
            type=str,
            default=None,
            help="file containing the remote share password",
        )
        cmd_parser.add_argument(
            "--port",
            dest="share_port",
            type=int,
            default=None,
            help="remote share port",
        )
        cmd_parser.add_argument(
            "--workgroup",
            dest="workgroup",
            type=str,
            default=None,
            help="remote share workgroup",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="POST the selected partition action; without it the command previews",
        )
        cmd_parser.add_argument(
            "--i-understand-irreversible",
            action="store_true",
            dest="confirm_irreversible",
            default=False,
            help="required with --confirm for delete, format, and initialize",
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
            "dell-vflash-partition",
            "command manage Dell VFlash partitions",
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
    def _dell_links(data):
        """Return ``Links.Oem.Dell`` from a Manager resource.

        :param data: Manager resource body.
        :return: Dell OEM link block, or an empty dict.
        """
        links = data.get("Links") if isinstance(data, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body, returning an empty dict on failures.

        :param uri: Redfish resource URI.
        :param do_async: run the query asynchronously when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _persistent_storage_uri(self, do_async):
        """Resolve DellPersistentStorageService from Manager OEM links.

        :param do_async: issue the Manager reads asynchronously when True.
        :return: discovered service URI, or the standard iDRAC fallback.
        """
        manager_ids = []
        try:
            manager_ids = self.discover_manager_ids() or []
        except Exception:
            manager_ids = []
        for manager_uri in manager_ids:
            manager = self._get(manager_uri, do_async)
            service_uri = self._link(
                self._dell_links(manager),
                "DellPersistentStorageService",
            )
            if service_uri:
                return service_uri
        return _SERVICE_FALLBACK

    def _persistent_storage_service(self, do_async):
        """Read DellPersistentStorageService.

        :param do_async: issue the service read asynchronously when True.
        :return: tuple of ``(uri, body)`` or ``(uri, CommandResult)`` on error.
        """
        uri = self._persistent_storage_uri(do_async)
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            return uri, CommandResult(None, None, None, f"failed to read {uri}: {exc}")
        return uri, data if isinstance(data, dict) else {}

    def _partition_action_rows(self, service):
        """Return available partition action rows and the discovered action map.

        :param service: DellPersistentStorageService resource body.
        :return: tuple of (rows, discovered actions).
        """
        actions = self.discover_redfish_actions(self, service)
        targets = self._flatten_action_targets(service)
        rows = []
        for spec in _ACTION_SPECS.values():
            target = targets.get(spec.full_type)
            if not target:
                continue
            action = actions.get(spec.action_name)
            allowed = {
                key: sorted(values or ())
                for key, values in (getattr(action, "args", {}) or {}).items()
            }
            rows.append({
                "Action": spec.selector,
                "FullType": spec.full_type,
                "Target": target,
                "Level": classify(spec.full_type).value,
                "RequiredParameters": list(spec.required),
                "OptionalParameters": list(spec.optional),
                "AllowableValues": allowed,
                "Description": spec.description,
            })
        return rows, actions

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
    def _required_payload_value(value, selector, parameter):
        """Return a required non-empty payload value.

        :param value: value supplied for a payload parameter.
        :param selector: selected action name for error text.
        :param parameter: Redfish action parameter name.
        :return: normalized value.
        :raises InvalidArgument: when the required value is missing or blank.
        """
        normalized = DellPersistentPartitionActions._optional_payload_value(value)
        if normalized is None:
            raise InvalidArgument(f"{selector} requires {_FLAG_NAMES[parameter]}")
        return normalized

    @staticmethod
    def _positive_int(value, selector, parameter, required=False):
        """Return a positive integer payload value.

        :param value: value supplied for an integer payload parameter.
        :param selector: selected action name for error text.
        :param parameter: Redfish action parameter name.
        :param required: when True, missing values raise InvalidArgument.
        :return: positive integer value, or None when optional and absent.
        :raises InvalidArgument: when the value is missing, non-integer, or non-positive.
        """
        if value is None:
            if required:
                raise InvalidArgument(f"{selector} requires {_FLAG_NAMES[parameter]}")
            return None
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise InvalidArgument(
                f"{_FLAG_NAMES[parameter]} must be a positive integer"
            ) from exc
        if number < 1:
            raise InvalidArgument(f"{_FLAG_NAMES[parameter]} must be a positive integer")
        return number

    @staticmethod
    def _partition_index(value, selector):
        """Return a validated Dell VFlash partition index.

        :param value: supplied partition index.
        :param selector: selected action name for error text.
        :return: integer partition index.
        :raises InvalidArgument: when missing or outside Dell's 1-16 range.
        """
        number = DellPersistentPartitionActions._positive_int(
            value,
            selector,
            "PartitionIndex",
            required=True,
        )
        if number > 16:
            raise InvalidArgument("--partition-index must be between 1 and 16")
        return number

    @staticmethod
    def _password_from_source(password_env=None, password_file=None):
        """Read a remote-share password from a named environment variable or file.

        :param password_env: name of the environment variable to read.
        :param password_file: path to a file containing the password.
        :return: the password string, or None when no source was provided.
        :raises InvalidArgument: when both sources are provided, the source is
            empty, the environment variable is absent, or the file cannot be read.
        """
        if password_env and password_file:
            raise InvalidArgument("use only one of --password-env or --password-file")
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
    def _payload(spec, values):
        """Build a DellPersistentStorageService action payload.

        :param spec: action selector metadata.
        :param values: normalized command arguments keyed by internal names.
        :return: JSON-serializable action payload.
        :raises InvalidArgument: when a required parameter is missing or invalid.
        """
        payload = {}
        for parameter in spec.required:
            value = values.get(_PAYLOAD_SOURCES[parameter])
            if parameter == "PartitionIndex":
                payload[parameter] = DellPersistentPartitionActions._partition_index(
                    value,
                    spec.selector,
                )
            elif parameter == "Size":
                payload[parameter] = DellPersistentPartitionActions._positive_int(
                    value,
                    spec.selector,
                    parameter,
                    required=True,
                )
            else:
                payload[parameter] = (
                    DellPersistentPartitionActions._required_payload_value(
                        value,
                        spec.selector,
                        parameter,
                    )
                )
        for parameter in spec.optional:
            value = values.get(_PAYLOAD_SOURCES[parameter])
            if parameter == "Port":
                value = DellPersistentPartitionActions._positive_int(
                    value,
                    spec.selector,
                    parameter,
                )
            else:
                value = DellPersistentPartitionActions._optional_payload_value(value)
            if value is not None:
                payload[parameter] = value
        return payload

    @staticmethod
    def _redact_password(result):
        """Mask Password in dry-run payloads before returning to callers.

        :param result: CommandResult from ``invoke_action``.
        :return: CommandResult with any dry-run password masked.
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
                action: Optional[str] = None,
                partition_index: Optional[int] = None,
                partition_type: Optional[str] = None,
                size: Optional[int] = None,
                size_unit: Optional[str] = None,
                os_volume_label: Optional[str] = None,
                format_type: Optional[str] = None,
                access_type: Optional[str] = None,
                share_type: Optional[str] = None,
                ip_address: Optional[str] = None,
                image_name: Optional[str] = None,
                share_path: Optional[str] = None,
                uri: Optional[str] = None,
                hash_type: Optional[str] = None,
                hash_value: Optional[str] = None,
                share_username: Optional[str] = None,
                password_env: Optional[str] = None,
                password_file: Optional[str] = None,
                share_port: Optional[int] = None,
                workgroup: Optional[str] = None,
                confirm: Optional[bool] = False,
                confirm_irreversible: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke DellPersistentStorageService partition actions.

        :param action: selected action from ``_ACTION_SPECS``; omit to list targets.
        :param partition_index: Dell VFlash partition index, 1-16.
        :param partition_type: partition type for create operations.
        :param size: blank partition size.
        :param size_unit: size unit for create.
        :param os_volume_label: OS volume label for create operations.
        :param format_type: filesystem type for format.
        :param access_type: access mode for modify.
        :param share_type: remote share type for image import/export.
        :param ip_address: remote share IP address or host.
        :param image_name: remote image name for image import/export.
        :param share_path: remote share path.
        :param uri: direct image URI for create-from-image.
        :param hash_type: image hash algorithm.
        :param hash_value: image hash value.
        :param share_username: optional remote share username.
        :param password_env: environment variable containing a remote share password.
        :param password_file: file containing a remote share password.
        :param share_port: optional remote share port.
        :param workgroup: optional remote share workgroup.
        :param confirm: authorize destructive actions to POST.
        :param confirm_irreversible: extra token for erase-class actions.
        :param dry_run: force preview mode even when confirmation is supplied.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with listing, preview, execution result, or error.
        """
        service_uri, service = self._persistent_storage_service(bool(do_async))
        if isinstance(service, CommandResult):
            return service

        rows, actions = self._partition_action_rows(service)
        if action is None:
            return CommandResult(
                {
                    "persistent_storage_service": service_uri,
                    "partition_actions": rows,
                },
                actions,
                None,
                None,
            )

        if action not in _ACTION_SPECS:
            return CommandResult(
                {
                    "persistent_storage_service": service_uri,
                    "available": rows,
                },
                actions,
                None,
                f"unknown Dell persistent-storage partition action: {action}",
            )

        spec = _ACTION_SPECS[action]
        if not any(row["Action"] == action for row in rows):
            return CommandResult(
                {
                    "persistent_storage_service": service_uri,
                    "available": rows,
                },
                actions,
                None,
                f"Dell persistent-storage partition action not found: {action}",
            )

        payload = self._payload(
            spec,
            {
                "partition_index": partition_index,
                "partition_type": partition_type,
                "size": size,
                "size_unit": size_unit,
                "os_volume_label": os_volume_label,
                "format_type": format_type,
                "access_type": access_type,
                "share_type": share_type,
                "ip_address": ip_address,
                "image_name": image_name,
                "share_path": share_path,
                "uri": uri,
                "hash_type": hash_type,
                "hash_value": hash_value,
                "share_username": share_username,
                "password": self._password_from_source(password_env, password_file),
                "share_port": share_port,
                "workgroup": workgroup,
            },
        )
        result = self.invoke_action(
            service_uri,
            spec.action_name,
            payload=payload,
            full_action_type=spec.full_type,
            do_async=bool(do_async),
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
            confirm_irreversible=bool(confirm_irreversible),
        )
        return self._redact_password(result)
