"""Start a scheduled Redfish UpdateService update batch.

    redfish_ctl update-start
    redfish_ctl update-start --dry_run
    redfish_ctl update-start --confirm

``#UpdateService.StartUpdate`` applies software images that were staged with
``OperationApplyTime=OnStartUpdateRequest``. Starting an update can flash
firmware or software, so this command is guarded: it resolves the advertised
action target and previews by default, and it only POSTs with ``--confirm``.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import REDFISH_API, ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_START_UPDATE_ACTION = "#UpdateService.StartUpdate"


class UpdateStart(RedfishManagerBase,
                  scm_type=ApiRequestType.UpdateStart,
                  name="update-start",
                  metaclass=Singleton):
    """Start UpdateService updates staged for OnStartUpdateRequest."""

    def __init__(self, *args, **kwargs):
        """Initialize the update-start command."""
        super(UpdateStart, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``update-start`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the UpdateService.StartUpdate POST; without it the "
                 "command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "update-start",
            "command start staged Redfish UpdateService updates",
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

    def _update_service_uri(self, do_async):
        """Resolve the UpdateService URI from the service root.

        :param do_async: issue the service-root query over the async Redfish path.
        :return: the UpdateService URI, or the standard fallback URI.
        """
        try:
            root = self.base_query(RedfishApi.Version, do_async=do_async).data or {}
        except Exception:
            root = {}
        return self._link(root, "UpdateService") or REDFISH_API.UpdateServiceQuery

    def execute(self,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Resolve and optionally invoke ``#UpdateService.StartUpdate``.

        :param confirm: authorize the StartUpdate POST to actually fire.
        :param dry_run: resolve the target and show it without POSTing;
            overrides ``confirm``.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with a dry-run preview, execution result, or
            missing-action error.
        """
        return self.invoke_action(
            self._update_service_uri(do_async),
            "StartUpdate",
            payload={},
            full_action_type=_START_UPDATE_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
