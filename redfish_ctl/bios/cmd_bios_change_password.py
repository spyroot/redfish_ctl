"""Change a BIOS password through the Redfish Bios.ChangePassword action.

    redfish_ctl bios-change-password
    redfish_ctl bios-change-password --password-name Administrator \
        --old-password-env OLD_BIOS_PASSWORD --new-password-env NEW_BIOS_PASSWORD
    redfish_ctl bios-change-password --password-name User \
        --old-password-file old.txt --new-password-file new.txt --confirm

The command discovers ``#Bios.ChangePassword`` from the host BIOS resource.
Changing a BIOS password can lock out platform configuration access, so the
action is DESTRUCTIVE: without ``--confirm`` the command only previews the POST
and redacts password values from returned data.

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
from ..redfish_shared import RedfishApi

_CHANGE_PASSWORD_ACTION = "#Bios.ChangePassword"


class BiosChangePassword(RedfishManagerBase,
                         scm_type=ApiRequestType.BiosChangePassword,
                         name="bios_change_password",
                         metaclass=Singleton):
    """Change a BIOS password through the discovered ChangePassword action."""

    def __init__(self, *args, **kwargs):
        """Initialize the bios-change-password command."""
        super(BiosChangePassword, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``bios-change-password`` subcommand.

        :param cls: the owning command class, used to build the base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--password-name",
            "--password_name",
            required=False,
            dest="password_name",
            type=str,
            default=None,
            help="BIOS password slot to change, such as Administrator or User",
        )
        old_group = cmd_parser.add_mutually_exclusive_group(required=False)
        old_group.add_argument(
            "--old-password-env",
            "--old_password_env",
            required=False,
            dest="old_password_env",
            type=str,
            default=None,
            help="environment variable containing the current BIOS password",
        )
        old_group.add_argument(
            "--old-password-file",
            "--old_password_file",
            required=False,
            dest="old_password_file",
            type=str,
            default=None,
            help="file containing the current BIOS password",
        )
        new_group = cmd_parser.add_mutually_exclusive_group(required=False)
        new_group.add_argument(
            "--new-password-env",
            "--new_password_env",
            required=False,
            dest="new_password_env",
            type=str,
            default=None,
            help="environment variable containing the new BIOS password",
        )
        new_group.add_argument(
            "--new-password-file",
            "--new_password_file",
            required=False,
            dest="new_password_file",
            type=str,
            default=None,
            help="file containing the new BIOS password",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the ChangePassword POST; without it the command only previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show it without POSTing",
        )
        return (
            cmd_parser,
            "bios-change-password",
            "command change a BIOS password",
        )

    def _bios_uri(self, do_async: bool) -> str:
        """Resolve the host BIOS resource URI from the ComputerSystem link.

        :param do_async: issue the host-system query over the async path when True.
        :return: the linked BIOS resource URI, or the standard ``/Bios`` fallback.
        """
        system_uri = self.idrac_manage_servers
        system = self.base_query(system_uri, do_async=do_async).data or {}
        bios_link = system.get("Bios") if isinstance(system, dict) else None
        if isinstance(bios_link, dict) and bios_link.get("@odata.id"):
            return bios_link["@odata.id"]
        return self._bios_fallback_uri(system_uri)

    @staticmethod
    def _bios_fallback_uri(system_uri: str) -> str:
        """Build the conventional BIOS URI under a ComputerSystem URI.

        :param system_uri: the ComputerSystem resource URI to base the path on.
        :return: the conventional ``<system_uri>/Bios`` resource URI.
        """
        return f"{system_uri.rstrip('/')}/{str(RedfishApi.Bios).strip('/')}"

    def _change_metadata(self, do_async: bool) -> CommandResult:
        """Return discovered Bios.ChangePassword target metadata.

        :param do_async: issue the BIOS query over the async path when True.
        :return: CommandResult with target metadata, or an error if absent.
        """
        uri = self._bios_uri(do_async)
        try:
            bios = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            return CommandResult(None, None, None, f"failed to read {uri}: {exc}")

        actions = self.discover_redfish_actions(self, bios)
        targets = self._flatten_action_targets(bios)
        target = targets.get(_CHANGE_PASSWORD_ACTION)
        if target is None:
            available = sorted(set(list(actions.keys()) + list(targets.keys())))
            return CommandResult(
                {"bios": uri, "action": _CHANGE_PASSWORD_ACTION, "available": available},
                actions,
                None,
                f"action '{_CHANGE_PASSWORD_ACTION}' not found on {uri}",
            )
        return CommandResult(
            {"bios": uri, "action": _CHANGE_PASSWORD_ACTION, "target": target},
            actions,
            None,
            None,
        )

    @staticmethod
    def _password_from_source(label: str,
                              env_name: Optional[str] = None,
                              file_name: Optional[str] = None) -> str:
        """Read a password value from a named environment variable or file.

        :param label: human-readable source label for error messages.
        :param env_name: name of the environment variable to read.
        :param file_name: path to a file containing the password.
        :return: the password string, including an empty string if the source is
            intentionally empty.
        :raises InvalidArgument: when source selection or reading fails.
        """
        if env_name and file_name:
            raise InvalidArgument(
                f"use only one of --{label}-password-env or --{label}-password-file"
            )
        if env_name is not None:
            name = env_name.strip()
            if not name:
                raise InvalidArgument(
                    f"{label} password environment variable name cannot be empty"
                )
            if name not in os.environ:
                raise InvalidArgument(f"{label} password environment variable '{name}' is not set")
            return os.environ[name]
        if file_name is not None:
            path = Path(file_name).expanduser()
            try:
                return path.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as exc:
                raise InvalidArgument(
                    f"failed to read {label} password file '{path}': {exc}"
                ) from exc
        raise InvalidArgument(f"{label} password source is required")

    @staticmethod
    def _payload(password_name: str, old_password: str, new_password: str) -> dict:
        """Build a Bios.ChangePassword payload.

        :param password_name: Redfish BIOS password slot name.
        :param old_password: current BIOS password value.
        :param new_password: replacement BIOS password value.
        :return: JSON-serializable action payload.
        :raises InvalidArgument: when ``password_name`` is empty.
        """
        name = (password_name or "").strip()
        if not name:
            raise InvalidArgument("password name cannot be empty")
        return {
            "PasswordName": name,
            "OldPassword": old_password,
            "NewPassword": new_password,
        }

    @staticmethod
    def _redact_passwords(result: CommandResult) -> CommandResult:
        """Mask password fields in dry-run or validation payloads.

        :param result: CommandResult from ``invoke_action``.
        :return: CommandResult with password fields redacted in returned data.
        """
        if not isinstance(result.data, dict):
            return result
        data = dict(result.data)
        payload = data.get("payload")
        if isinstance(payload, dict):
            payload = dict(payload)
            if "OldPassword" in payload:
                payload["OldPassword"] = "********"
            if "NewPassword" in payload:
                payload["NewPassword"] = "********"
            data["payload"] = payload
        return CommandResult(data, result.discovered, result.extra, result.error)

    @staticmethod
    def _has_password_input(password_name: Optional[str],
                            old_password_env: Optional[str],
                            old_password_file: Optional[str],
                            new_password_env: Optional[str],
                            new_password_file: Optional[str]) -> bool:
        """Return whether the caller supplied any password-change input.

        :param password_name: requested BIOS password slot.
        :param old_password_env: old password environment variable name.
        :param old_password_file: old password file path.
        :param new_password_env: new password environment variable name.
        :param new_password_file: new password file path.
        :return: True when at least one change parameter was supplied.
        """
        return any((
            password_name is not None,
            old_password_env is not None,
            old_password_file is not None,
            new_password_env is not None,
            new_password_file is not None,
        ))

    def execute(self,
                password_name: Optional[str] = None,
                old_password_env: Optional[str] = None,
                old_password_file: Optional[str] = None,
                new_password_env: Optional[str] = None,
                new_password_file: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke the BIOS ChangePassword action.

        With no password-change inputs the command returns the discovered action
        target without mutating. With inputs it requires a password name plus old
        and new password sources, then invokes the DESTRUCTIVE action through the
        shared dry-run/confirm guard.

        :param password_name: BIOS password slot, for example Administrator or User.
        :param old_password_env: environment variable containing the old password.
        :param old_password_file: file containing the old password.
        :param new_password_env: environment variable containing the new password.
        :param new_password_file: file containing the new password.
        :param confirm: authorize the ChangePassword POST to actually fire.
        :param dry_run: force a dry-run preview even when ``confirm`` is true.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: a CommandResult with target metadata, dry-run preview, or POST result.
        """
        if not self._has_password_input(
            password_name,
            old_password_env,
            old_password_file,
            new_password_env,
            new_password_file,
        ):
            return self._change_metadata(bool(do_async))

        payload = self._payload(
            password_name,
            self._password_from_source("old", old_password_env, old_password_file),
            self._password_from_source("new", new_password_env, new_password_file),
        )
        result = self.invoke_action(
            self._bios_uri(bool(do_async)),
            "ChangePassword",
            payload=payload,
            full_action_type=_CHANGE_PASSWORD_ACTION,
            do_async=do_async,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        return self._redact_passwords(result)
