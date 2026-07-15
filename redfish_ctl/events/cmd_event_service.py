"""Read EventService and subscription summary."""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class EventServiceQuery(RedfishManagerBase,
                        scm_type=ApiRequestType.EventServiceQuery,
                        name='event-service',
                        metaclass=Singleton):
    """Read Redfish EventService, SSE, and subscription metadata."""

    def __init__(self, *args, **kwargs):
        super(EventServiceQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only event-service subcommand."""
        cmd_parser = cls.base_parser()
        help_text = "command read EventService and subscription state"
        return cmd_parser, "event-service", help_text

    @staticmethod
    def _link(data, key):
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _status_value(data, key):
        status = (data or {}).get("Status")
        return status.get(key) if isinstance(status, dict) else None

    def _get(self, uri, do_async):
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _subscriptions(self, uri, do_async):
        if not uri:
            return {"uri": None, "count": None, "members": []}

        data = self._get(uri, do_async)
        members = data.get("Members") if isinstance(data, dict) else None
        if not isinstance(members, list):
            members = []
        return {
            "uri": uri,
            "count": data.get("Members@odata.count") if isinstance(data, dict) else None,
            "members": [
                member.get("@odata.id")
                for member in members
                if isinstance(member, dict) and member.get("@odata.id")
            ],
        }

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Read EventService state without opening or creating subscriptions."""
        service = self.base_query(
            REDFISH_API.EventServiceQuery,
            filename=filename,
            do_async=do_async,
            do_expanded=do_expanded,
        ).data or {}
        subscriptions_uri = self._link(service, "Subscriptions")
        data = {
            "Id": service.get("Id"),
            "Name": service.get("Name"),
            "ServiceEnabled": service.get("ServiceEnabled"),
            "Health": self._status_value(service, "Health"),
            "State": self._status_value(service, "State"),
            "ServerSentEventUri": service.get("ServerSentEventUri"),
            "SSEFilterPropertiesSupported": (
                service.get("SSEFilterPropertiesSupported") or {}
            ),
            "EventFormatTypes": service.get("EventFormatTypes") or [],
            "EventTypesForSubscription": (
                service.get("EventTypesForSubscription") or []
            ),
            "RegistryPrefixes": service.get("RegistryPrefixes") or [],
            "ResourceTypes": service.get("ResourceTypes") or [],
            "Subscriptions": self._subscriptions(subscriptions_uri, do_async),
        }
        return CommandResult(data, None, None, None)
