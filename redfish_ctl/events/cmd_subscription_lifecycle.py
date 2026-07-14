"""Create and delete Redfish EventDestination subscriptions."""

import json
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import IDRAC_API, ApiRequestType, RedfishJsonSpec, Singleton
from ..redfish_manager import CommandResult


def _as_list(values) -> list[str]:
    if values is None:
        return []
    raw_values = [values] if isinstance(values, str) else list(values)
    normalized = []
    for raw in raw_values:
        for value in str(raw).split(","):
            item = value.strip()
            if item:
                normalized.append(item)
    return normalized


class _SubscriptionBase(IDracManager):
    """Shared EventService subscription helpers."""

    @staticmethod
    def _link(data, key):
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    def _subscription_collection_uri(self, do_async):
        service = self.base_query(
            IDRAC_API.EventServiceQuery,
            do_async=do_async,
        ).data or {}
        subscriptions_uri = self._link(service, "Subscriptions")
        if not subscriptions_uri:
            raise InvalidArgument("EventService Subscriptions link is not available")
        return subscriptions_uri

    def _subscription_members(self, subscriptions_uri, do_async):
        collection = self.base_query(subscriptions_uri, do_async=do_async).data or {}
        members = collection.get("Members")
        if not isinstance(members, list):
            members = []
        return [
            member.get("@odata.id")
            for member in members
            if isinstance(member, dict) and member.get("@odata.id")
        ]

    def _resolve_subscription_uri(self, subscription, subscriptions_uri, do_async):
        if not subscription or not str(subscription).strip():
            raise InvalidArgument("subscription id or URI is required")
        value = str(subscription).strip()
        if value.startswith("/redfish/"):
            prefix = subscriptions_uri.rstrip("/") + "/"
            if value != subscriptions_uri and not value.startswith(prefix):
                raise InvalidArgument(
                    f"subscription URI must be under {subscriptions_uri}"
                )
            return value

        for member_uri in self._subscription_members(subscriptions_uri, do_async):
            if member_uri.rsplit("/", 1)[-1] == value or member_uri == value:
                return member_uri
        raise InvalidArgument(f"subscription {value!r} was not found")


class SubscriptionCreate(_SubscriptionBase,
                         scm_type=ApiRequestType.SubscriptionCreate,
                         name='subscription-create',
                         metaclass=Singleton):
    """Create an EventDestination subscription after dry-run preview."""

    def __init__(self, *args, **kwargs):
        super(SubscriptionCreate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded subscription-create subcommand."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--destination", required=True, dest="destination", metavar="URI",
            help="EventDestination listener URI")
        cmd_parser.add_argument(
            "--protocol", default="Redfish", dest="protocol",
            help="EventDestination protocol; default is Redfish")
        cmd_parser.add_argument(
            "--event-format-type", default=None, dest="event_format_type",
            help="optional EventFormatType such as Event or MetricReport")
        cmd_parser.add_argument(
            "--event-type", action="append", dest="event_types", default=None,
            metavar="TYPE", help="optional EventTypes value; repeat or comma-separate")
        cmd_parser.add_argument(
            "--registry-prefix", action="append", dest="registry_prefixes",
            default=None, metavar="PREFIX",
            help="optional RegistryPrefixes value; repeat or comma-separate")
        cmd_parser.add_argument(
            "--resource-type", action="append", dest="resource_types",
            default=None, metavar="TYPE",
            help="optional ResourceTypes value; repeat or comma-separate")
        cmd_parser.add_argument(
            "--context", default=None, dest="context",
            help="optional EventDestination Context string")
        cmd_parser.add_argument(
            "--confirm", action="store_true", dest="confirm", default=False,
            help="create the subscription; without it the command only previews")
        help_text = "create an EventService EventDestination subscription"
        return cmd_parser, "subscription-create", help_text

    @staticmethod
    def _payload(destination, protocol, event_format_type, event_types,
                 registry_prefixes, resource_types, context):
        if not destination or not str(destination).strip():
            raise InvalidArgument("destination URI is required")
        if not protocol or not str(protocol).strip():
            raise InvalidArgument("subscription protocol is required")

        payload = {
            "Destination": str(destination).strip(),
            "Protocol": str(protocol).strip(),
        }
        if event_format_type:
            payload["EventFormatType"] = str(event_format_type).strip()
        normalized_event_types = _as_list(event_types)
        if normalized_event_types:
            payload["EventTypes"] = normalized_event_types
        normalized_registry_prefixes = _as_list(registry_prefixes)
        if normalized_registry_prefixes:
            payload["RegistryPrefixes"] = normalized_registry_prefixes
        normalized_resource_types = _as_list(resource_types)
        if normalized_resource_types:
            payload["ResourceTypes"] = normalized_resource_types
        if context:
            payload["Context"] = str(context)
        return payload

    def _post_subscription(self, target, payload):
        headers = {}
        headers.update(self.json_content_type)
        response = self.api_post_call(
            f"{self._default_method}{self.redfish_ip}{target}",
            json.dumps(payload),
            headers,
        )
        status = self.default_post_success(response, expected=201)
        location = response.headers.get(RedfishJsonSpec.Location)
        return status, location

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                destination: Optional[str] = None,
                protocol: Optional[str] = "Redfish",
                event_format_type: Optional[str] = None,
                event_types=None,
                registry_prefixes=None,
                resource_types=None,
                context: Optional[str] = None,
                confirm: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or create an EventDestination subscription."""
        target = self._subscription_collection_uri(do_async)
        payload = self._payload(
            destination,
            protocol,
            event_format_type,
            event_types,
            registry_prefixes,
            resource_types,
            context,
        )
        if not confirm:
            return CommandResult({
                "dry_run": True,
                "action": "create",
                "target": target,
                "payload": payload,
                "note": "preview only; re-run with --confirm to create subscription",
            }, None, None, None)

        if do_async:
            result, status = self.base_post(
                target,
                payload=payload,
                do_async=do_async,
                expected_status=201,
            )
            location = None
            error = result.error
        else:
            status, location = self._post_subscription(target, payload)
            error = None
        data = {
            "action": "create",
            "target": target,
            "status": str(status),
            "location": location,
            "error": error,
        }
        return CommandResult(data, None, None, error)


class SubscriptionDelete(_SubscriptionBase,
                         scm_type=ApiRequestType.SubscriptionDelete,
                         name='subscription-delete',
                         metaclass=Singleton):
    """Delete an EventDestination subscription after dry-run preview."""

    def __init__(self, *args, **kwargs):
        super(SubscriptionDelete, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded subscription-delete subcommand."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--subscription", required=True, dest="subscription", metavar="ID_OR_URI",
            help="subscription member id or /redfish/v1/EventService/Subscriptions URI")
        cmd_parser.add_argument(
            "--confirm", action="store_true", dest="confirm", default=False,
            help="delete the subscription; without it the command only previews")
        help_text = "delete an EventService EventDestination subscription"
        return cmd_parser, "subscription-delete", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                subscription: Optional[str] = None,
                confirm: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Preview or delete an EventDestination subscription."""
        subscriptions_uri = self._subscription_collection_uri(do_async)
        target = self._resolve_subscription_uri(
            subscription,
            subscriptions_uri,
            do_async,
        )
        if not confirm:
            return CommandResult({
                "dry_run": True,
                "action": "delete",
                "target": target,
                "note": "preview only; re-run with --confirm to delete subscription",
            }, None, None, None)

        result, status = self.base_delete(
            target,
            do_async=do_async,
            expected_status=200,
        )
        data = {
            "action": "delete",
            "target": target,
            "status": str(status),
            "error": result.error,
        }
        return CommandResult(data, None, None, result.error)
