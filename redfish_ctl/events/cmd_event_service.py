"""Read EventService and subscription summary.

    redfish_ctl event-service
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class EventServiceQuery(IDracManager,
                        scm_type=ApiRequestType.EventServiceQuery,
                        name='event-service',
                        metaclass=Singleton):
    """Read Redfish EventService, SSE, and subscription metadata."""

    def __init__(self, *args, **kwargs):
        """Initialize the event-service command."""
        super(EventServiceQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only event-service subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command read EventService and subscription state"
        return cmd_parser, "event-service", help_text

    @staticmethod
    def _link(data, key):
        """Return the ``@odata.id`` of the linked resource under ``key``.

        :param data: the resource dict holding the link.
        :param key: the property name of the link to resolve.
        :return: the linked ``@odata.id``, or None when the link is absent.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _status_value(data, key):
        """Return a field from a resource's ``Status`` sub-object.

        :param data: the resource dict holding the ``Status`` block.
        :param key: the ``Status`` field to read (e.g. Health or State).
        :return: the field value, or None when ``Status`` or the field is absent.
        """
        status = (data or {}).get("Status")
        return status.get(key) if isinstance(status, dict) else None

    def _get(self, uri, do_async):
        """Query a URI and return its data, swallowing errors as an empty dict.

        :param uri: the Redfish resource URI to fetch.
        :param do_async: issue the query over the async Redfish path when True.
        :return: the response data dict, or an empty dict on any failure.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _subscriptions(self, uri, do_async):
        """Summarize the Subscriptions collection at ``uri``.

        :param uri: the Subscriptions collection URI, or falsy when unavailable.
        :param do_async: issue the query over the async Redfish path when True.
        :return: a dict with the collection ``uri``, member ``count``, and member URIs.
        """
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
        """Read EventService state without opening or creating subscriptions.

        :param filename: if set, save the EventService response to this file.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the queries over the async Redfish path when True.
        :param do_expanded: issue an expanded ($expand) EventService query when True.
        :return: a CommandResult with the EventService and subscription summary.
        """
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
