"""Insert a comment or worknote into the Dell Lifecycle Controller log.

    redfish_ctl dell-lc-log-comment
    redfish_ctl dell-lc-log-comment --comment "maintenance note"
    redfish_ctl dell-lc-log-comment --comment "follow-up" --log-sequence-number 123 --confirm

The command resolves ``#DellLCService.InsertCommentInLCLog`` from the Dell LC
service. With no comment it lists the discovered action target. With a comment it
previews by default; ``--confirm`` is required before the POST is sent.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_LC_LOG_COMMENT_ACTION = "#DellLCService.InsertCommentInLCLog"
_STANDARD_LC_SERVICE = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
_LEGACY_LC_SERVICE = f"{RedfishApi.Version}/Dell/Managers/iDRAC.Embedded.1/DellLCService"


class DellLcLogComment(RedfishManagerBase,
                       scm_type=ApiRequestType.DellLcLogComment,
                       name="dell-lc-log-comment",
                       metaclass=Singleton):
    """Preview or insert a Dell Lifecycle Controller log comment."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-lc-log-comment command."""
        super(DellLcLogComment, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-lc-log-comment`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--comment",
            required=False,
            dest="comment",
            type=str,
            default=None,
            help="comment/worknote text to insert into the Lifecycle Controller log",
        )
        cmd_parser.add_argument(
            "--log-sequence-number",
            required=False,
            dest="log_sequence_number",
            type=str,
            default=None,
            help="optional LC log sequence number to attach the comment to",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the InsertCommentInLCLog POST; without it the command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show it without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-lc-log-comment",
            "command insert a Dell Lifecycle Controller log comment",
        )

    @staticmethod
    def _nested_link(data, *keys):
        """Return an ``@odata.id`` nested under ``keys``.

        :param data: resource body to inspect.
        :param keys: dictionary path ending at a Redfish link object.
        :return: link target URI, or None when absent or malformed.
        """
        node = data or {}
        for key in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node.get("@odata.id") if isinstance(node, dict) else None

    def _linked_lc_service_uri(self, do_async):
        """Resolve DellLCService from Manager Links/Oem/Dell when available.

        :param do_async: issue manager reads over the async Redfish path.
        :return: linked DellLCService URI, or None when no manager exposes it.
        """
        try:
            manager_ids = self.discover_manager_ids()
        except Exception:
            manager_ids = []
        for manager_uri in manager_ids:
            try:
                manager = self.base_query(manager_uri, do_async=do_async).data or {}
            except Exception:
                continue
            linked = self._nested_link(
                manager, "Links", "Oem", "Dell", "DellLCService"
            )
            if linked:
                return linked
        return None

    def _service_candidates(self, do_async):
        """Return candidate DellLCService URIs in preferred order.

        :param do_async: issue discovery reads over the async Redfish path.
        :return: tuple of unique candidate service URIs.
        """
        candidates = [
            self._linked_lc_service_uri(do_async),
            _STANDARD_LC_SERVICE,
            _LEGACY_LC_SERVICE,
        ]
        result = []
        for uri in candidates:
            if uri and uri not in result:
                result.append(uri)
        return tuple(result)

    def _read_lc_service(self, do_async):
        """Read DellLCService using linked and legacy path candidates.

        :param do_async: issue queries over the async Redfish path.
        :return: tuple of ``(uri, body)`` or a CommandResult error.
        """
        errors = []
        for uri in self._service_candidates(do_async):
            try:
                result = self.base_query(uri, do_async=do_async)
            except Exception as exc:
                errors.append(f"{uri}: {exc}")
                continue
            if result.error is not None:
                errors.append(f"{uri}: {result.error}")
                continue
            data = result.data or {}
            if isinstance(data, dict) and data:
                return uri, data
        error = "; ".join(errors) if errors else "no DellLCService candidate URI"
        return CommandResult(None, None, None, f"failed to read DellLCService: {error}")

    def _comment_metadata(self, do_async):
        """Return discovered InsertCommentInLCLog target metadata.

        :param do_async: issue the DellLCService query over the async path.
        :return: CommandResult with target metadata, or an error if absent.
        """
        service_info = self._read_lc_service(do_async)
        if isinstance(service_info, CommandResult):
            return service_info
        uri, service = service_info
        actions = self.discover_redfish_actions(self, service)
        target = self._flatten_action_targets(service).get(_LC_LOG_COMMENT_ACTION)
        if target is None:
            available = sorted(set(list(actions.keys())
                                   + list(self._flatten_action_targets(service).keys())))
            return CommandResult(
                {
                    "lc_service": uri,
                    "action": _LC_LOG_COMMENT_ACTION,
                    "available": available,
                },
                actions,
                None,
                f"action '{_LC_LOG_COMMENT_ACTION}' not found on {uri}",
            )
        return CommandResult(
            {
                "lc_service": uri,
                "action": _LC_LOG_COMMENT_ACTION,
                "target": target,
            },
            actions,
            None,
            None,
        )

    @staticmethod
    def _payload(comment, log_sequence_number=None):
        """Build the InsertCommentInLCLog payload.

        :param comment: worknote/comment text.
        :param log_sequence_number: optional LC log sequence number.
        :return: JSON-serializable action payload.
        :raises InvalidArgument: when ``comment`` is empty after trimming.
        """
        text = (comment or "").strip()
        if not text:
            raise InvalidArgument("Lifecycle Controller log comment cannot be empty")
        payload = {"Comment": text}
        sequence = (log_sequence_number or "").strip()
        if sequence:
            payload["LogSequenceNumber"] = sequence
        return payload

    def execute(self,
                comment: Optional[str] = None,
                log_sequence_number: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke the Dell LC log-comment action.

        With no ``--comment`` this command only reports the discovered
        InsertCommentInLCLog target. With a comment it resolves the target and
        previews the POST unless ``--confirm`` is passed. ``--dry_run`` remains a
        no-POST override even with ``--confirm``.

        :param comment: worknote/comment text to insert; None lists target metadata.
        :param log_sequence_number: optional LC log sequence number to annotate.
        :param confirm: authorize the action POST to actually fire.
        :param dry_run: resolve the target and show the payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: a CommandResult with target metadata, the action outcome, or a
            blocked/dry-run preview.
        :raises InvalidArgument: when ``comment`` is empty after trimming.
        """
        metadata = self._comment_metadata(do_async)
        if comment is None or metadata.error is not None:
            return metadata

        service_uri = metadata.data["lc_service"]
        return self.invoke_action(
            service_uri,
            "InsertCommentInLCLog",
            payload=self._payload(comment, log_sequence_number),
            full_action_type=_LC_LOG_COMMENT_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
