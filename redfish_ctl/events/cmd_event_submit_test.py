"""Submit a Redfish test event (EventService.SubmitTestEvent).

    idrac_ctl event-submit-test --message_id Alert.1.0.TestEvent

Fires a synthetic event through the box's EventService so you can verify an
event subscription / SSE pipeline end to end. Reversible (it just emits one
event), so it runs without ``--confirm``.

The action target is discovered from the EventService's own Actions block via
the shared ``invoke_action`` primitive — no hardcoded URL — so it is
vendor-neutral. Different controllers require different payload fields; pass the
ones your box's ``@Redfish.ActionInfo`` lists.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishApi


class EventSubmitTest(IDracManager,
                      scm_type=ApiRequestType.EventSubmitTest,
                      name='event_submit_test',
                      metaclass=Singleton):
    """Submit a test event via EventService.SubmitTestEvent."""

    def __init__(self, *args, **kwargs):
        super(EventSubmitTest, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``event-submit-test`` subcommand."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--message_id', required=False, dest='message_id', type=str,
            default="Alert.1.0.TestEvent",
            help="MessageId to put in the test event payload")
        cmd_parser.add_argument(
            '--event_type', required=False, dest='event_type', type=str,
            default=None, help="optional EventType (e.g. Alert) if the box requires it")
        cmd_parser.add_argument(
            '--dry_run', action='store_true', dest='dry_run',
            help="resolve the target and show the payload without POSTing")
        return cmd_parser, "event-submit-test", "command submit a Redfish test event"

    def _event_service_uri(self, do_async):
        """Resolve the EventService URI from the service root, with a standard fallback."""
        try:
            root = self.base_query(RedfishApi.Version, do_async=do_async).data or {}
        except Exception:
            root = {}
        link = root.get("EventService")
        if isinstance(link, dict) and link.get("@odata.id"):
            return link["@odata.id"]
        return f"{RedfishApi.Version}/EventService"

    def execute(self,
                message_id: Optional[str] = "Alert.1.0.TestEvent",
                event_type: Optional[str] = None,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Discover SubmitTestEvent and POST a minimal test-event payload.

        REVERSIBLE per the action policy, so it executes by default; ``--dry_run``
        still shows the resolved target + payload without POSTing.
        """
        payload = {"MessageId": message_id}
        if event_type:
            payload["EventType"] = event_type
        return self.invoke_action(
            self._event_service_uri(do_async),
            "SubmitTestEvent",
            payload=payload,
            full_action_type="#EventService.SubmitTestEvent",
            do_async=do_async,
            dry_run=bool(dry_run),
        )
