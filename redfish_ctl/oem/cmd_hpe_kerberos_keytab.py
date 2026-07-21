"""Import an HPE iLO Kerberos keytab through AccountService.

    redfish_ctl hpe-kerberos-keytab-import
    redfish_ctl hpe-kerberos-keytab-import --keytab-file ./krb5.keytab --dry_run
    redfish_ctl hpe-kerberos-keytab-import --keytab-base64-env HPE_KEYTAB_B64 --confirm

The command discovers ``#HpeiLOAccountService.ImportKerberosKeytab`` from the
AccountService resource. Keytab material is accepted only from an environment
variable or file source, and returned previews redact the payload value.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import base64
import os
from abc import abstractmethod
from binascii import Error as BinasciiError
from pathlib import Path
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_IMPORT_KEYTAB_ACTION = "#HpeiLOAccountService.ImportKerberosKeytab"
_KEYTAB_FIELD = "KerberosKeytab"


class HpeKerberosKeytabImport(
    RedfishManagerBase,
    scm_type=ApiRequestType.HpeKerberosKeytabImport,
    name="hpe-kerberos-keytab-import",
    metaclass=Singleton,
):
    """Import a Kerberos keytab into HPE iLO AccountService."""

    def __init__(self, *args, **kwargs):
        """Initialize the hpe-kerberos-keytab-import command."""
        super(HpeKerberosKeytabImport, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``hpe-kerberos-keytab-import`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        keytab_group = cmd_parser.add_mutually_exclusive_group(required=False)
        keytab_group.add_argument(
            "--keytab-base64-env",
            dest="keytab_base64_env",
            default=None,
            help="environment variable containing Base64-encoded keytab material",
        )
        keytab_group.add_argument(
            "--keytab-file",
            dest="keytab_file",
            default=None,
            help="keytab file to read and Base64-encode before import",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the HPE ImportKerberosKeytab POST; otherwise preview only",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show a redacted payload without POSTing",
        )
        return (
            cmd_parser,
            "hpe-kerberos-keytab-import",
            "command import an HPE iLO Kerberos keytab",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property.

        :param data: resource body holding the link.
        :param key: link property name.
        :return: linked URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    def _account_service_uri(self, do_async):
        """Resolve AccountService from the service root.

        :param do_async: issue the root query over the async path when True.
        :return: AccountService URI, with the standard path as fallback.
        """
        try:
            root = self.base_query(RedfishApi.Version, do_async=do_async).data or {}
        except Exception:
            root = {}
        return self._link(root, "AccountService") or f"{RedfishApi.Version}/AccountService"

    def _metadata(self, do_async):
        """Return the discovered HPE keytab import target metadata.

        :param do_async: issue the AccountService query asynchronously when True.
        :return: CommandResult with target metadata or a discovery error.
        """
        uri = self._account_service_uri(do_async)
        try:
            service = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            return CommandResult(None, None, None, f"failed to read {uri}: {exc}")

        actions = self.discover_redfish_actions(self, service)
        target = self._flatten_action_targets(service).get(_IMPORT_KEYTAB_ACTION)
        if target is None:
            available = sorted(set(
                list(actions.keys())
                + list(self._flatten_action_targets(service).keys())
            ))
            return CommandResult(
                {
                    "account_service": uri,
                    "action": _IMPORT_KEYTAB_ACTION,
                    "available": available,
                },
                actions,
                None,
                f"action '{_IMPORT_KEYTAB_ACTION}' not found on {uri}",
            )
        return CommandResult(
            {
                "account_service": uri,
                "action": _IMPORT_KEYTAB_ACTION,
                "target": target,
                "payload_field": _KEYTAB_FIELD,
            },
            actions,
            None,
            None,
        )

    @staticmethod
    def _normalize_base64(value, source):
        """Validate and normalize Base64 keytab text.

        :param value: Base64 text to validate.
        :param source: human-readable source label for error messages.
        :return: compact Base64 text.
        :raises InvalidArgument: when the value is empty or invalid Base64.
        """
        normalized = "".join(str(value or "").split())
        if not normalized:
            raise InvalidArgument(f"{source} cannot be empty")
        try:
            base64.b64decode(normalized.encode("ascii"), validate=True)
        except (BinasciiError, ValueError, UnicodeEncodeError) as exc:
            raise InvalidArgument(f"{source} must contain Base64-encoded keytab data") from exc
        return normalized

    @classmethod
    def _keytab_from_source(cls, keytab_base64_env=None, keytab_file=None):
        """Read keytab material from one source and return Base64 text.

        :param keytab_base64_env: env var containing Base64-encoded keytab data.
        :param keytab_file: file containing raw keytab bytes.
        :return: Base64-encoded keytab text.
        :raises InvalidArgument: when no source is supplied or a source is invalid.
        """
        if keytab_base64_env and keytab_file:
            raise InvalidArgument(
                "use only one of --keytab-base64-env or --keytab-file"
            )
        if keytab_base64_env is not None:
            env_name = keytab_base64_env.strip()
            if not env_name:
                raise InvalidArgument("keytab environment variable name cannot be empty")
            if env_name not in os.environ:
                raise InvalidArgument(f"keytab environment variable '{env_name}' is not set")
            return cls._normalize_base64(
                os.environ[env_name],
                f"environment variable '{env_name}'",
            )
        if keytab_file is not None:
            path = Path(keytab_file).expanduser()
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise InvalidArgument(f"failed to read keytab file '{path}': {exc}") from exc
            if not raw:
                raise InvalidArgument(f"keytab file '{path}' cannot be empty")
            return base64.b64encode(raw).decode("ascii")
        raise InvalidArgument("one of --keytab-base64-env or --keytab-file is required")

    @staticmethod
    def _redact_keytab(result):
        """Mask KerberosKeytab in dry-run payloads before returning.

        :param result: CommandResult returned by ``invoke_action``.
        :return: CommandResult with any keytab payload value masked.
        """
        if not isinstance(result.data, dict):
            return result
        payload = result.data.get("payload")
        if isinstance(payload, dict) and _KEYTAB_FIELD in payload:
            payload = dict(payload)
            payload[_KEYTAB_FIELD] = "********"
            result.data["payload"] = payload
        return result

    def execute(
        self,
        keytab_base64_env: Optional[str] = None,
        keytab_file: Optional[str] = None,
        confirm: Optional[bool] = False,
        dry_run: Optional[bool] = False,
        filename: Optional[str] = None,
        data_type: Optional[str] = "json",
        verbose: Optional[bool] = False,
        do_async: Optional[bool] = False,
        **kwargs,
    ) -> CommandResult:
        """List the import target, or import a Kerberos keytab.

        With no keytab source the command lists the discovered target without
        mutating. With a source it resolves the HPE import action and uses the
        shared action guard, so the POST fires only with ``--confirm``.

        :param keytab_base64_env: env var containing Base64-encoded keytab data.
        :param keytab_file: raw keytab file to read and Base64-encode.
        :param confirm: authorize the POST to fire.
        :param dry_run: force preview mode even when ``confirm`` is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying reads/POST over the async path when True.
        :return: CommandResult with target metadata, preview, execution result, or error.
        """
        if keytab_base64_env is None and keytab_file is None:
            return self._metadata(do_async)

        result = self.invoke_action(
            self._account_service_uri(do_async),
            "ImportKerberosKeytab",
            payload={
                _KEYTAB_FIELD: self._keytab_from_source(
                    keytab_base64_env=keytab_base64_env,
                    keytab_file=keytab_file,
                )
            },
            full_action_type=_IMPORT_KEYTAB_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        return self._redact_keytab(result)
