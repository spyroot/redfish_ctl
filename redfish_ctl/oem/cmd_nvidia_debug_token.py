"""Preview or invoke NVIDIA debug-token Redfish actions.

    redfish_ctl nvidia-debug-token
    redfish_ctl nvidia-debug-token --action generate --confirm
    redfish_ctl nvidia-debug-token --action install --token-env DEBUG_TOKEN

The command discovers ``#NvidiaDebugToken.*`` targets from each ComputerSystem's
``Oem.Nvidia.CPUDebugToken`` link. All selected actions preview by default;
``--confirm`` is required before any POST is sent. Install token material is
read from an environment variable or file and masked in returned previews.

Author Mus spyroot@gmail.com
"""
import os
from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi


@dataclass(frozen=True)
class _DebugTokenAction:
    """Selector metadata for one NVIDIA debug-token action."""

    selector: str
    full_type: str
    action_name: str
    description: str


_ACTION_SPECS = {
    "disable": _DebugTokenAction(
        selector="disable",
        full_type="#NvidiaDebugToken.DisableToken",
        action_name="DisableToken",
        description="disable the currently installed debug token",
    ),
    "generate": _DebugTokenAction(
        selector="generate",
        full_type="#NvidiaDebugToken.GenerateToken",
        action_name="GenerateToken",
        description="generate a new debug token request",
    ),
    "install": _DebugTokenAction(
        selector="install",
        full_type="#NvidiaDebugToken.InstallToken",
        action_name="InstallToken",
        description="install a provided debug token",
    ),
}


class NvidiaDebugToken(IDracManager,
                       scm_type=ApiRequestType.NvidiaDebugToken,
                       name="nvidia-debug-token",
                       metaclass=Singleton):
    """Discover and invoke NVIDIA debug-token Redfish actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the nvidia-debug-token command."""
        super(NvidiaDebugToken, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``nvidia-debug-token`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--action",
            choices=sorted(_ACTION_SPECS),
            default=None,
            help="debug-token action to preview or invoke; omit to list targets",
        )
        cmd_parser.add_argument(
            "--system",
            default=None,
            help="ComputerSystem Id or URI when multiple systems expose a token",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific CPUDebugToken resource URI to target",
        )
        cmd_parser.add_argument(
            "--token-type",
            dest="token_type",
            default=None,
            help="optional TokenType payload for the generate action",
        )
        token_group = cmd_parser.add_mutually_exclusive_group(required=False)
        token_group.add_argument(
            "--token-env",
            dest="token_env",
            default=None,
            help="environment variable containing the token for install",
        )
        token_group.add_argument(
            "--token-file",
            dest="token_file",
            default=None,
            help="file containing the token for install",
        )
        cmd_parser.add_argument(
            "--token-field",
            dest="token_field",
            default="Token",
            help="payload field name for install token material; default: Token",
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
            "nvidia-debug-token",
            "command run NVIDIA debug-token actions",
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
        :return: linked URI, or None when any step is absent.
        """
        value = data
        for key in keys:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return NvidiaDebugToken._link({"value": value}, "value")

    @staticmethod
    def _action_info(data, full_type):
        """Return an action's ActionInfo link when advertised.

        :param data: debug-token resource body.
        :param full_type: full ``#Type.Action`` name.
        :return: ActionInfo URI, or None.
        """
        actions = data.get("Actions") if isinstance(data, dict) else None
        action = actions.get(full_type) if isinstance(actions, dict) else None
        if not isinstance(action, dict):
            return None
        info = action.get("@Redfish.ActionInfo")
        return info if isinstance(info, str) and info else None

    @staticmethod
    def _resource_id(uri):
        """Return the trailing Redfish URI segment.

        :param uri: Redfish resource URI.
        :return: trailing URI segment.
        """
        return uri.rstrip("/").rsplit("/", 1)[-1]

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

    def _discover_rows(self, do_async):
        """Discover CPUDebugToken resources and advertised actions.

        :param do_async: issue GET requests on the async path when True.
        :return: list of discovered debug-token rows.
        """
        systems = self._get(f"{RedfishApi.Version}/Systems", do_async)
        rows = []
        for system_uri in self._members(systems):
            system = self._get(system_uri, do_async, optional=True)
            token_uri = self._nested_link(
                system,
                "Oem",
                "Nvidia",
                "CPUDebugToken",
            )
            if not token_uri:
                continue
            token = self._get(token_uri, do_async, optional=True)
            targets = self._flatten_action_targets(token)
            actions = []
            for spec in _ACTION_SPECS.values():
                target = targets.get(spec.full_type)
                if target:
                    actions.append({
                        "Action": spec.selector,
                        "FullType": spec.full_type,
                        "Target": target,
                        "ActionInfo": self._action_info(token, spec.full_type),
                        "Description": spec.description,
                    })
            if not actions:
                continue
            rows.append({
                "System": system.get("Id") or self._resource_id(system_uri),
                "SystemUri": system_uri,
                "Id": token.get("Id") or self._resource_id(token_uri),
                "Name": token.get("Name"),
                "Status": token.get("Status"),
                "TokenType": token.get("TokenType"),
                "Uri": token_uri,
                "Actions": actions,
            })
        return rows

    @staticmethod
    def _resolve_row(rows, system=None, resource_uri=None):
        """Resolve a selected debug-token row.

        :param rows: discovered rows from :meth:`_discover_rows`.
        :param system: optional ComputerSystem Id or URI.
        :param resource_uri: optional CPUDebugToken resource URI.
        :return: matching row.
        :raises InvalidArgument: when selection is missing or ambiguous.
        """
        matches = list(rows)
        if resource_uri:
            wanted = resource_uri.rstrip("/")
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
            raise InvalidArgument("NVIDIA debug-token resource not found")
        if len(matches) > 1:
            systems = [row["System"] for row in matches]
            raise InvalidArgument(
                "multiple NVIDIA debug-token resources found; pass --system "
                f"or --resource-uri: {systems}"
            )
        return matches[0]

    @staticmethod
    def _token_from_source(token_env=None, token_file=None):
        """Read install token material from an environment variable or file.

        :param token_env: environment variable name.
        :param token_file: file path containing the token.
        :return: token string, or None when no source is supplied.
        :raises InvalidArgument: when the source is invalid or unreadable.
        """
        if token_env and token_file:
            raise InvalidArgument("use only one of --token-env or --token-file")
        if token_env is not None:
            env_name = token_env.strip()
            if not env_name:
                raise InvalidArgument("token environment variable name cannot be empty")
            if env_name not in os.environ:
                raise InvalidArgument(f"token environment variable '{env_name}' is not set")
            return os.environ[env_name]
        if token_file is not None:
            path = Path(token_file).expanduser()
            try:
                return path.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as exc:
                raise InvalidArgument(f"failed to read token file '{path}': {exc}") from exc
        return None

    @staticmethod
    def _payload(action,
                 token_type=None,
                 token_env=None,
                 token_file=None,
                 token_field="Token"):
        """Build a debug-token action payload.

        :param action: selected action name.
        :param token_type: optional TokenType for generate.
        :param token_env: environment variable containing install token data.
        :param token_file: file containing install token data.
        :param token_field: payload key to use for install token data.
        :return: payload dict.
        :raises InvalidArgument: when arguments do not match the action.
        """
        if token_type and action != "generate":
            raise InvalidArgument("--token-type is only valid with --action generate")
        if action != "install" and (token_env or token_file):
            raise InvalidArgument("token material is only valid with --action install")
        if action == "generate":
            value = (token_type or "").strip()
            return {"TokenType": value} if value else {}
        if action == "install":
            field = (token_field or "").strip()
            if not field:
                raise InvalidArgument("token payload field cannot be empty")
            token = NvidiaDebugToken._token_from_source(token_env, token_file)
            if token is None:
                raise InvalidArgument(
                    "--action install requires --token-env or --token-file"
                )
            return {field: token}
        return {}

    @staticmethod
    def _redact_payload(result, token_field):
        """Mask install token material in returned payloads.

        :param result: CommandResult from ``invoke_action``.
        :param token_field: payload key holding token material.
        :return: CommandResult with token material masked.
        """
        if not isinstance(result.data, dict):
            return result
        payload = result.data.get("payload")
        field = (token_field or "Token").strip()
        if isinstance(payload, dict) and field in payload:
            redacted = dict(payload)
            redacted[field] = "********"
            result.data["payload"] = redacted
        return result

    def execute(self,
                action: Optional[str] = None,
                system: Optional[str] = None,
                resource_uri: Optional[str] = None,
                token_type: Optional[str] = None,
                token_env: Optional[str] = None,
                token_file: Optional[str] = None,
                token_field: Optional[str] = "Token",
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke NVIDIA debug-token actions.

        :param action: optional action selector; None lists discovered targets.
        :param system: optional ComputerSystem Id or URI selector.
        :param resource_uri: optional CPUDebugToken URI selector.
        :param token_type: optional TokenType payload for generate.
        :param token_env: environment variable holding install token material.
        :param token_file: file holding install token material.
        :param token_field: payload key for install token material.
        :param confirm: authorize a POST. Without this every selected action previews.
        :param dry_run: force a preview even with ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying Redfish calls on the async path.
        :return: CommandResult with discovered targets, preview, or POST result.
        :raises InvalidArgument: when selection or payload arguments are invalid.
        """
        rows = self._discover_rows(bool(do_async))
        if action is None:
            return CommandResult({"debug_token_targets": rows}, None, None, None)

        spec = _ACTION_SPECS[action]
        row = self._resolve_row(rows, system=system, resource_uri=resource_uri)
        result = self.invoke_action(
            row["Uri"],
            spec.action_name,
            payload=self._payload(
                action,
                token_type=token_type,
                token_env=token_env,
                token_file=token_file,
                token_field=token_field,
            ),
            full_action_type=spec.full_type,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        if action == "install":
            return self._redact_payload(result, token_field)
        return result
