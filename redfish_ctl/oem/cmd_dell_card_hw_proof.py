"""Verify Dell iDRAC card-service hardware proof of possession.

    redfish_ctl dell-card-hw-proof
    redfish_ctl dell-card-hw-proof --dry_run
    redfish_ctl dell-card-hw-proof --algorithm AES128CBC \\
        --key-derivation-function DellSHA256 --confirm

The command discovers ``DelliDRACCardService`` from Manager OEM links and
resolves the advertised ``#DelliDRACCardService.VerifyHWProofOfPossession``
target from that resource. It lists the target by default and previews the
payload unless ``--confirm`` is supplied.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi

_ACTION_TYPE = "#DelliDRACCardService.VerifyHWProofOfPossession"
_ACTION_NAME = "VerifyHWProofOfPossession"
_SERVICE_NAME = "DelliDRACCardService"
_DEFAULT_SERVICE_URI = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/{_SERVICE_NAME}"
)


class DellCardHwProof(IDracManager,
                      scm_type=ApiRequestType.DellCardHwProof,
                      name="dell-card-hw-proof",
                      metaclass=Singleton):
    """Discover and invoke Dell hardware proof-of-possession verification."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-card-hw-proof command."""
        super(DellCardHwProof, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-card-hw-proof`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--algorithm",
            dest="algorithm",
            default=None,
            help="Algorithm payload value; defaults to the advertised singleton",
        )
        cmd_parser.add_argument(
            "--key-derivation-function",
            dest="key_derivation_function",
            default=None,
            help=(
                "KeyDerivationFunction payload value; defaults to the "
                "advertised singleton"
            ),
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DelliDRACCardService URI when discovery needs override",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the proof verification instead of previewing it",
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
            "dell-card-hw-proof",
            "command verify Dell iDRAC hardware proof of possession",
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
    def _oem_dell(data):
        """Return the ``Oem.Dell`` extension block from a resource.

        :param data: Redfish resource body.
        :return: Dell OEM block, or an empty dict.
        """
        oem = data.get("Oem") if isinstance(data, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

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
        :param do_async: issue the query on the async path when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _service_uris(self, do_async):
        """Return candidate DelliDRACCardService resource URIs.

        :param do_async: issue Manager queries on the async path when True.
        :return: ordered list of candidate service URIs.
        """
        uris = []
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            manager = self._get(manager_uri, do_async)
            service_uri = self._link(
                self._links_oem_dell(manager),
                _SERVICE_NAME,
            )
            if not service_uri:
                service_uri = self._link(self._oem_dell(manager), _SERVICE_NAME)
            if service_uri:
                uris.append(service_uri)
        if not uris:
            uris.append(_DEFAULT_SERVICE_URI)
        return list(dict.fromkeys(uri.rstrip("/") for uri in uris))

    @staticmethod
    def _action_value(service, parameter):
        """Return allowable values for one proof action parameter.

        :param service: DelliDRACCardService resource body.
        :param parameter: Redfish action parameter name.
        :return: list of allowable strings advertised by the service.
        """
        actions = service.get("Actions") if isinstance(service, dict) else None
        action = actions.get(_ACTION_TYPE) if isinstance(actions, dict) else None
        if not isinstance(action, dict):
            return []
        values = action.get(f"{parameter}@Redfish.AllowableValues") or []
        return list(values) if isinstance(values, list) else []

    def _row_for(self, service_uri, do_async):
        """Build a discovered hardware proof target row for one service URI.

        :param service_uri: candidate DelliDRACCardService URI.
        :param do_async: issue the service query on the async path when True.
        :return: discovered row, or None when the action is absent.
        """
        service = self._get(service_uri, do_async)
        if not service:
            return None
        target = self._flatten_action_targets(service).get(_ACTION_TYPE)
        if not target:
            return None
        return {
            "Resource": service_uri,
            "Action": _ACTION_TYPE,
            "Target": target,
            "AllowedAlgorithms": self._action_value(service, "Algorithm"),
            "AllowedKeyDerivationFunctions": self._action_value(
                service,
                "KeyDerivationFunction",
            ),
        }

    def _discover_rows(self, do_async, resource_uri=None):
        """Discover Dell hardware proof verification targets.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DelliDRACCardService URI.
        :return: list of discovered target rows.
        """
        uris = [resource_uri.rstrip("/")] if resource_uri else self._service_uris(
            do_async
        )
        rows = []
        for uri in dict.fromkeys(uris):
            row = self._row_for(uri, do_async)
            if row:
                rows.append(row)
        return rows

    @staticmethod
    def _default_value(value, allowed, field):
        """Return a caller value or the resource's singleton default.

        :param value: caller-provided value.
        :param allowed: advertised allowable values.
        :param field: payload field name for error messages.
        :return: payload value.
        :raises ValueError: when no value is available.
        """
        if value:
            return value
        if len(allowed) == 1:
            return allowed[0]
        raise ValueError(f"{field} is required")

    def _payload_for(self, row, algorithm, key_derivation_function):
        """Build the VerifyHWProofOfPossession payload.

        :param row: discovered target row with allowable values.
        :param algorithm: optional Algorithm value.
        :param key_derivation_function: optional KeyDerivationFunction value.
        :return: action payload.
        """
        return {
            "Algorithm": self._default_value(
                algorithm,
                row["AllowedAlgorithms"],
                "Algorithm",
            ),
            "KeyDerivationFunction": self._default_value(
                key_derivation_function,
                row["AllowedKeyDerivationFunctions"],
                "KeyDerivationFunction",
            ),
        }

    def execute(self,
                algorithm: Optional[str] = None,
                key_derivation_function: Optional[str] = None,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List, preview, or invoke Dell hardware proof verification.

        :param algorithm: optional Algorithm payload value.
        :param key_derivation_function: optional KeyDerivationFunction value.
        :param resource_uri: optional service URI to disambiguate targets.
        :param confirm: authorize a POST. Without this the action previews.
        :param dry_run: force a preview even with ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying Redfish calls on the async path.
        :return: CommandResult with discovered targets, preview, or POST result.
        """
        rows = self._discover_rows(bool(do_async), resource_uri=resource_uri)
        should_preview = any((algorithm, key_derivation_function, dry_run, confirm))
        if not should_preview:
            return CommandResult({"hw_proof_targets": rows}, None, None, None)

        if not rows:
            return CommandResult(
                {"action": _ACTION_TYPE, "available": []},
                None,
                None,
                "Dell hardware proof action not found",
            )
        if len(rows) > 1:
            return CommandResult(
                {"matches": rows},
                None,
                None,
                "multiple Dell hardware proof targets found; pass --resource-uri",
            )

        try:
            payload = self._payload_for(rows[0], algorithm, key_derivation_function)
        except ValueError as exc:
            return CommandResult({"matches": rows}, None, None, str(exc))

        result = self.invoke_action(
            rows[0]["Resource"],
            _ACTION_NAME,
            payload=payload,
            full_action_type=_ACTION_TYPE,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        if not confirm and isinstance(result.data, dict):
            result.data["requires_confirm"] = True
            result.data["blocked"] = (
                "Dell hardware proof verification requires --confirm"
            )
        return result
