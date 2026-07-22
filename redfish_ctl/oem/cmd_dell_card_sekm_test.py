"""Preview or run the Dell SEKM server connectivity test action.

    redfish_ctl dell-card-sekm-test
    redfish_ctl dell-card-sekm-test --server-type Primary
    redfish_ctl dell-card-sekm-test --server-type Secondary --confirm

The command discovers the Dell ``DelliDRACCardService`` link from Manager OEM
metadata and resolves the advertised
``#DelliDRACCardService.TestSEKMServerConnection`` target from that resource.
Selected tests preview by default; ``--confirm`` is required before POSTing.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton

_ACTION_TYPE = "#DelliDRACCardService.TestSEKMServerConnection"
_ACTION_NAME = "TestSEKMServerConnection"
_SERVICE_NAME = "DelliDRACCardService"
_DEFAULT_SERVICE_URI = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService"
)
_DEFAULT_ALLOWED_SERVER_TYPES = ("Primary", "Secondary")


class DellCardSekmTest(IDracManager,
                       scm_type=ApiRequestType.DellCardSekmTest,
                       name="dell-card-sekm-test",
                       metaclass=Singleton):
    """Discover and invoke the Dell SEKM server connectivity test."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-card-sekm-test command."""
        super(DellCardSekmTest, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-card-sekm-test`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--server-type",
            dest="server_type",
            choices=_DEFAULT_ALLOWED_SERVER_TYPES,
            default=None,
            help="SEKM server endpoint to test; omit to list available targets",
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
            help="POST the selected connectivity test instead of previewing it",
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
            "dell-card-sekm-test",
            "command test Dell SEKM server connectivity",
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
    def _dell_oem_links(manager):
        """Return the Dell block under ``Manager.Links.Oem``.

        :param manager: Manager resource body.
        :return: Dell OEM links dict, or an empty dict.
        """
        links = manager.get("Links") if isinstance(manager, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    @staticmethod
    def _dell_oem_body(manager):
        """Return the Dell block under ``Manager.Oem``.

        :param manager: Manager resource body.
        :return: Dell OEM body dict, or an empty dict.
        """
        oem = manager.get("Oem") if isinstance(manager, dict) else None
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

        :param do_async: issue manager queries on the async path when True.
        :return: list of candidate service URIs.
        """
        uris = []
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            manager = self._get(manager_uri, do_async)
            service_uri = self._link(
                self._dell_oem_links(manager),
                _SERVICE_NAME,
            )
            if not service_uri:
                service_uri = self._link(
                    self._dell_oem_body(manager),
                    _SERVICE_NAME,
                )
            if service_uri:
                uris.append(service_uri)
        if not uris:
            uris.append(_DEFAULT_SERVICE_URI)
        return list(dict.fromkeys(uris))

    @staticmethod
    def _allowed_server_types(service, actions):
        """Return allowed ``ServerType`` values for the SEKM test action.

        :param service: DelliDRACCardService resource body.
        :param actions: discovered Redfish action map for ``service``.
        :return: sorted list of allowed server type strings.
        """
        action = actions.get(_ACTION_NAME)
        allowed = tuple(getattr(action, "args", {}).get("ServerType", ()) or ())
        if not allowed:
            raw_actions = service.get("Actions") if isinstance(service, dict) else None
            raw_action = (
                raw_actions.get(_ACTION_TYPE)
                if isinstance(raw_actions, dict)
                else None
            )
            if isinstance(raw_action, dict):
                allowed = tuple(
                    raw_action.get("ServerType@Redfish.AllowableValues", ()) or ()
                )
        if not allowed:
            allowed = _DEFAULT_ALLOWED_SERVER_TYPES
        return sorted(allowed)

    def _row_for(self, resource_uri, do_async):
        """Build a discovered action row for one DelliDRACCardService URI.

        :param resource_uri: candidate service URI.
        :param do_async: issue the service query on the async path when True.
        :return: discovered row, or None when the action is absent.
        """
        service = self._get(resource_uri, do_async)
        if not service:
            return None
        target = self._flatten_action_targets(service).get(_ACTION_TYPE)
        if not target:
            return None
        actions = self.discover_redfish_actions(self, service)
        return {
            "Resource": resource_uri,
            "Action": _ACTION_TYPE,
            "Target": target,
            "AllowedServerTypes": self._allowed_server_types(service, actions),
        }

    def _discover_rows(self, do_async, resource_uri=None):
        """Discover Dell SEKM server test targets.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DelliDRACCardService URI.
        :return: list of discovered target rows.
        """
        uris = [resource_uri] if resource_uri else self._service_uris(do_async)
        rows = []
        for uri in dict.fromkeys(uris):
            row = self._row_for(uri, do_async)
            if row:
                rows.append(row)
        return rows

    def execute(self,
                server_type: Optional[str] = None,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke the Dell SEKM server connectivity test.

        :param server_type: optional ``ServerType`` value, usually Primary or Secondary.
        :param resource_uri: optional DelliDRACCardService URI override.
        :param confirm: authorize a POST. Without this the selected test previews.
        :param dry_run: force a preview even with ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying Redfish calls on the async path.
        :return: CommandResult with discovered targets, preview, or POST result.
        """
        rows = self._discover_rows(bool(do_async), resource_uri=resource_uri)
        if server_type is None:
            return CommandResult({"sekm_test_targets": rows}, None, None, None)

        if not rows:
            return CommandResult(
                {"action": _ACTION_TYPE, "available": []},
                None,
                None,
                "Dell card SEKM test action not found",
            )
        if len(rows) > 1:
            return CommandResult(
                {"matches": rows},
                None,
                None,
                "multiple Dell card SEKM test targets found; pass --resource-uri",
            )

        result = self.invoke_action(
            rows[0]["Resource"],
            _ACTION_NAME,
            payload={"ServerType": server_type},
            full_action_type=_ACTION_TYPE,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        if not confirm and isinstance(result.data, dict):
            result.data["requires_confirm"] = True
            result.data["blocked"] = "Dell SEKM server test requires --confirm"
        return result
