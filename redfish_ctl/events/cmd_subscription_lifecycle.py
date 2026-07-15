"""Create and delete Redfish EventDestination subscriptions.

    redfish_ctl subscription-create --destination https://listener/events --protocol Redfish --confirm
    redfish_ctl subscription-delete --subscription <id-or-uri> --confirm
"""

import asyncio
import inspect
import json
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import (
    REDFISH_API,
    ApiRequestType,
    HTTPMethod,
    RedfishApiRespond,
    Singleton,
)


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


class _SubscriptionBase(RedfishManagerBase):
    """Shared EventService subscription helpers."""

    @staticmethod
    def _link(data, key):
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _ensure_event_loop():
        """Return the current asyncio event loop, creating one if none is set.

        :return: an asyncio event loop usable for the async Redfish calls.
        """
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    def _subscription_collection_uri(self, do_async):
        """Resolve the EventService Subscriptions collection URI.

        :param do_async: prime the async event loop when True.
        :return: the Subscriptions collection URI.
        :raises InvalidArgument: when the EventService Subscriptions link is absent.
        """
        if do_async:
            self._ensure_event_loop()
        service = self.base_query(
            REDFISH_API.EventServiceQuery,
            do_async=do_async,
        ).data or {}
        subscriptions_uri = self._link(service, "Subscriptions")
        if not subscriptions_uri:
            raise InvalidArgument("EventService Subscriptions link is not available")
        return subscriptions_uri

    def _subscription_members(self, subscriptions_uri, do_async):
        """List the member URIs in the Subscriptions collection.

        :param subscriptions_uri: the Subscriptions collection URI.
        :param do_async: prime the async event loop when True.
        :return: the ``@odata.id`` of each subscription member (possibly empty).
        """
        if do_async:
            self._ensure_event_loop()
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
        """Resolve a subscription id or URI to a full collection-member URI.

        :param subscription: a subscription id or a full member URI.
        :param subscriptions_uri: the Subscriptions collection URI.
        :param do_async: prime the async event loop when True.
        :return: the resolved subscription member URI.
        :raises InvalidArgument: when empty, when the value is the collection URI
            itself, when it falls outside the collection, or when it is not found.
        """
        if not subscription or not str(subscription).strip():
            raise InvalidArgument("subscription id or URI is required")
        value = str(subscription).strip()
        if value.startswith("/redfish/"):
            if value.rstrip("/") == subscriptions_uri.rstrip("/"):
                raise InvalidArgument(
                    f"subscription member URI under {subscriptions_uri} is required"
                )
            prefix = subscriptions_uri.rstrip("/") + "/"
            if not value.startswith(prefix):
                raise InvalidArgument(
                    f"subscription URI must be under {subscriptions_uri}"
                )
            return value

        for member_uri in self._subscription_members(subscriptions_uri, do_async):
            if member_uri.rsplit("/", 1)[-1] == value or member_uri == value:
                return member_uri
        raise InvalidArgument(f"subscription {value!r} was not found")

    @staticmethod
    def _location(response):
        """Return the ``Location`` header (the new subscription URI) of a response.

        :param response: the HTTP response from a create request.
        :return: the Location header value, or None when it is absent.
        """
        if response is None or response.headers is None:
            return None
        return response.headers.get("Location")

    def _response_status(self, response, expected_status):
        """Map an HTTP status code to a RedfishApiRespond result.

        :param response: the HTTP response to classify.
        :param expected_status: the status a success is expected to return.
        :return: Success on the expected status, Ok on any other 2xx, else Error.
        """
        mapped_status = self._http_code_mapping.get(response.status_code)
        if response.status_code == expected_status:
            return mapped_status or RedfishApiRespond.Success
        if 200 <= response.status_code < 300:
            return mapped_status or RedfishApiRespond.Ok
        return RedfishApiRespond.Error

    def _error_text(self, response):
        """Extract a human-readable error message from an error response.

        :param response: the HTTP response to parse.
        :return: the parsed error text, or the exception text if parsing fails.
        """
        try:
            return str(self.parse_error(response))
        except Exception as exc:
            return str(exc)

    @staticmethod
    async def _await_async_response(response_or_future):
        """Await an async Redfish call, unwrapping a doubly-awaitable result.

        :param response_or_future: the awaitable returned by an async HTTP call.
        :return: the resolved HTTP response.
        """
        response = await response_or_future
        if inspect.isawaitable(response):
            return await response
        return response

    def _send_subscription_request(self, method, request, body, headers, do_async):
        """Send a subscription create/delete over the sync or async HTTP path.

        :param method: the HTTP method — POST to create, DELETE to remove.
        :param request: the fully-qualified request URL.
        :param body: the JSON-serializable request body (ignored for DELETE).
        :param headers: the request headers.
        :param do_async: run the call on the async event loop when True.
        :return: the HTTP response object.
        :raises InvalidArgument: when the method is neither POST nor DELETE.
        """
        if not do_async:
            if method == HTTPMethod.POST:
                return self.api_post_call(request, json.dumps(body), headers)
            if method == HTTPMethod.DELETE:
                return self.api_delete_call(request, headers)
        loop = self._ensure_event_loop()
        if method == HTTPMethod.POST:
            return loop.run_until_complete(
                self._await_async_response(
                    self.api_async_post_call(
                        loop,
                        request,
                        json.dumps(body),
                        headers,
                    )
                )
            )
        if method == HTTPMethod.DELETE:
            return loop.run_until_complete(
                self._await_async_response(
                    self.api_async_delete_call(loop, request, "", headers)
                )
            )
        raise InvalidArgument(f"unsupported subscription method {method}")

    def _subscription_mutation(
        self,
        method,
        target,
        action,
        payload=None,
        expected_status=204,
        do_async=False,
    ):
        """Run a subscription create or delete and shape the CommandResult.

        :param method: the HTTP method — POST to create, DELETE to remove.
        :param target: the subscription collection URI (create) or member URI (delete).
        :param action: a short label for the operation, used in messages.
        :param payload: the request body for a create (None for a delete).
        :param expected_status: the HTTP status a success returns (201 create / 204 delete).
        :param do_async: run the call on the async event loop when True.
        :return: a CommandResult with the outcome (and the Location URI on create).
        """
        body = payload or {}
        headers = {}
        headers.update(self.json_content_type)
        request = f"{self._default_method}{self.redfish_ip}{target}"
        response = None
        try:
            response = self._send_subscription_request(
                method,
                request,
                body,
                headers,
                do_async,
            )
        except Exception as exc:
            error = str(exc)
            data = {
                "action": action,
                "target": target,
                "status": str(RedfishApiRespond.Error),
                "status_code": None,
                "error": error,
            }
            if method == HTTPMethod.POST:
                data["location"] = None
            return CommandResult(data, None, None, error), RedfishApiRespond.Error

        status = self._response_status(response, expected_status)
        error = None if status != RedfishApiRespond.Error else self._error_text(response)
        data = {
            "action": action,
            "target": target,
            "status": str(status),
            "status_code": response.status_code,
            "error": error,
        }
        if method == HTTPMethod.POST:
            data["location"] = self._location(response)
        return CommandResult(data, None, None, error), status


class SubscriptionCreate(_SubscriptionBase,
                         scm_type=ApiRequestType.SubscriptionCreate,
                         name='subscription-create',
                         metaclass=Singleton):
    """Create an EventDestination subscription after dry-run preview."""

    def __init__(self, *args, **kwargs):
        super(SubscriptionCreate, self).__init__(*args, **kwargs)

    @staticmethod
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
        """Build the EventDestination create body from the CLI options.

        :param destination: the subscriber URL events are delivered to (required).
        :param protocol: the event protocol, e.g. Redfish (required).
        :param event_format_type: the delivered payload format, or None to omit.
        :param event_types: event types to subscribe to (list or comma string).
        :param registry_prefixes: message-registry prefixes to filter on.
        :param resource_types: resource types to filter on.
        :param context: an opaque context string echoed back on each event.
        :return: the subscription request body dict.
        :raises InvalidArgument: when destination or protocol is missing.
        """
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
        """Preview (dry-run) or create an EventDestination subscription.

        :param filename: optional path to save the result payload to.
        :param data_type: output serialization format (``json`` or ``yaml``).
        :param verbose: emit extra diagnostics when True.
        :param do_async: issue the create over the async Redfish path when True.
        :param do_expanded: request an expanded ($expand) response where supported.
        :param destination: subscriber URL events are delivered to (required to create).
        :param protocol: the event protocol (default ``Redfish``).
        :param event_format_type: the delivered payload format, or None to omit.
        :param event_types: event types to subscribe to (list or comma string).
        :param registry_prefixes: message-registry prefixes to filter on.
        :param resource_types: resource types to filter on.
        :param context: an opaque context string echoed back on each event.
        :param confirm: actually create; without it the command only previews.
        :return: a CommandResult with the created subscription, or the dry-run preview.
        """
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

        result, _ = self._subscription_mutation(
            HTTPMethod.POST,
            target,
            "create",
            payload=payload,
            expected_status=201,
            do_async=do_async,
        )
        return result


class SubscriptionDelete(_SubscriptionBase,
                         scm_type=ApiRequestType.SubscriptionDelete,
                         name='subscription-delete',
                         metaclass=Singleton):
    """Delete an EventDestination subscription after dry-run preview."""

    def __init__(self, *args, **kwargs):
        """Construct the subscription-delete command (delegates to the base)."""
        super(SubscriptionDelete, self).__init__(*args, **kwargs)

    @staticmethod
    def register_subcommand(cls):
        """Register the guarded subscription-delete subcommand."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--subscription", required=True, dest="subscription", metavar="ID_OR_URI",
            help="subscription member id or member URI")
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
        """Preview (dry-run) or delete an EventDestination subscription.

        :param filename: optional path to save the result payload to.
        :param data_type: output serialization format (``json`` or ``yaml``).
        :param verbose: emit extra diagnostics when True.
        :param do_async: issue the delete over the async Redfish path when True.
        :param do_expanded: request an expanded ($expand) response where supported.
        :param subscription: the subscription id or member URI to remove (required).
        :param confirm: actually delete; without it the command only previews.
        :return: a CommandResult with the delete outcome, or the dry-run preview.
        """
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

        result, _ = self._subscription_mutation(
            HTTPMethod.DELETE,
            target,
            "delete",
            expected_status=204,
            do_async=do_async,
        )
        return result
