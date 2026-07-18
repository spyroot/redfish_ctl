"""Fetch Dell iDRAC KVM session status through DelliDRACCardService.

    redfish_ctl dell-kvm-session
    redfish_ctl dell-kvm-session --query
    redfish_ctl dell-kvm-session --query --dry_run

The command discovers ``#DelliDRACCardService.GetKVMSession`` from the Manager
OEM Dell card-service link. With no query flag it lists the discovered target.
The selected action is a read-only status query carried over POST.

Author Mus spyroot@gmail.com
"""
import asyncio
import json
from abc import abstractmethod
from typing import Optional

from ..actions.action_policy import classify
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_DELL_KVM_SESSION_ACTION = "#DelliDRACCardService.GetKVMSession"
_DELL_CARD_SERVICE_FALLBACK = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService"
)


class DellKvmSession(RedfishManagerBase,
                     scm_type=ApiRequestType.DellKvmSession,
                     name="dell-kvm-session",
                     metaclass=Singleton):
    """Discover and query Dell iDRAC KVM session state."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-kvm-session command."""
        super(DellKvmSession, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-kvm-session`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--query",
            action="store_true",
            dest="query",
            default=False,
            help="POST the read-only GetKVMSession query; omit to list target metadata",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target without POSTing",
        )
        return (
            cmd_parser,
            "dell-kvm-session",
            "command fetch Dell iDRAC KVM session status",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property.

        :param data: resource body containing the link.
        :param key: property name whose ``@odata.id`` to extract.
        :return: the linked URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @classmethod
    def _dell_oem_link(cls, manager, key):
        """Return an OEM Dell link from a Manager resource.

        :param manager: Manager resource body.
        :param key: OEM Dell link name, such as ``DelliDRACCardService``.
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

    def _card_service_uri(self, do_async):
        """Resolve the DelliDRACCardService URI from Manager OEM links.

        :param do_async: issue Manager queries over the async Redfish path.
        :return: discovered DelliDRACCardService URI, or the legacy Dell fallback.
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
            target = self._dell_oem_link(manager, "DelliDRACCardService")
            if target:
                return target
        return _DELL_CARD_SERVICE_FALLBACK

    def _card_service(self, do_async):
        """Read the DelliDRACCardService resource.

        :param do_async: issue the query over the async Redfish path.
        :return: tuple of ``(uri, body)`` or ``(uri, CommandResult)`` on error.
        """
        uri = self._card_service_uri(do_async)
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            return uri, CommandResult(None, None, None, f"failed to read {uri}: {exc}")
        return uri, data

    def _session_metadata(self, do_async):
        """Return discovered GetKVMSession action metadata.

        :param do_async: issue the card-service query over the async Redfish path.
        :return: CommandResult with service and action target metadata.
        """
        uri, service = self._card_service(do_async)
        if isinstance(service, CommandResult):
            return service
        actions = self.discover_redfish_actions(self, service)
        target = self._flatten_action_targets(service).get(_DELL_KVM_SESSION_ACTION)
        if target is None:
            available = sorted(set(list(actions.keys())
                                   + list(self._flatten_action_targets(service).keys())))
            return CommandResult(
                {
                    "card_service": uri,
                    "action": _DELL_KVM_SESSION_ACTION,
                    "available": available,
                },
                actions,
                None,
                f"action '{_DELL_KVM_SESSION_ACTION}' not found on {uri}",
            )
        return CommandResult(
            {
                "card_service": uri,
                "action": _DELL_KVM_SESSION_ACTION,
                "target": target,
            },
            actions,
            None,
            None,
        )

    def _post_session_query(self, target, do_async):
        """POST the read-only KVM session query and keep the response body.

        :param target: discovered GetKVMSession action target.
        :param do_async: issue the POST over the async Redfish path.
        :return: CommandResult with the response payload and action metadata.
        """
        headers = dict(self.json_content_type)
        url = f"{self._default_method}{self.redfish_ip}{target}"
        try:
            if do_async:
                loop = asyncio.get_event_loop()
                api_resp, response = loop.run_until_complete(
                    self.api_async_post_until_complete(
                        url,
                        json.dumps({}),
                        headers,
                        expected=200,
                    )
                )
            else:
                response = self.api_post_call(url, json.dumps({}), headers)
                api_resp = self.default_post_success(response, expected=200)
        except Exception as exc:
            return CommandResult(
                {
                    "action": _DELL_KVM_SESSION_ACTION,
                    "target": target,
                    "payload": {},
                    "level": classify(_DELL_KVM_SESSION_ACTION).value,
                },
                None,
                None,
                f"failed to POST {target}: {exc}",
            )

        try:
            response_body = response.json()
        except Exception:
            response_body = {}
        data = response_body if isinstance(response_body, dict) else {
            "response": response_body,
        }
        data.setdefault("Status", self.api_success_msg(api_resp)["Status"])
        data.setdefault("executed", True)
        data.setdefault("method", "POST")
        data.setdefault("action", _DELL_KVM_SESSION_ACTION)
        data.setdefault("target", target)
        data.setdefault("level", classify(_DELL_KVM_SESSION_ACTION).value)
        return CommandResult(data, None, None, None)

    def execute(self,
                query: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or query Dell iDRAC KVM session state.

        :param query: when True, POST the read-only GetKVMSession query.
        :param dry_run: resolve the target without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: a CommandResult with target metadata, dry-run preview, or POST result.
        """
        metadata = self._session_metadata(do_async)
        if not query or metadata.error:
            return metadata

        target = metadata.data["target"]
        if dry_run:
            return CommandResult(
                {
                    "dry_run": True,
                    "action": _DELL_KVM_SESSION_ACTION,
                    "target": target,
                    "payload": {},
                    "level": classify(_DELL_KVM_SESSION_ACTION).value,
                    "blocked": None,
                },
                metadata.discovered,
                None,
                None,
            )
        result = self._post_session_query(target, bool(do_async))
        return CommandResult(result.data, metadata.discovered, None, result.error)
