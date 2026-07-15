"""Report the console access each Manager exposes (serial / graphical / shell).

    redfish_ctl console-info

Walks every Manager and reports its ``SerialConsole``, ``GraphicalConsole`` and
``CommandShell`` blocks: {Manager, Console, Enabled, ConnectTypes, MaxSessions}.

Redfish *describes* console access; it does not stream it. Use the reported
connect types to reach the live console out of band — SerialConsole via SOL
(``ipmitool sol activate`` or ``ssh`` to the BMC) and GraphicalConsole via the
KVM/IP viewer. This command tells an operator what's available and how.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class ConsoleInfo(RedfishManagerBase,
                  scm_type=ApiRequestType.ConsoleInfo,
                  name='console-info',
                  metaclass=Singleton):
    """Report serial / graphical / shell console access for every manager."""

    def __init__(self, *args, **kwargs):
        """Initialize the console-info command."""
        super(ConsoleInfo, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``console-info`` subcommand (read-only).

        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        help_text = "command report console access (serial/graphical/shell) per manager"
        return cmd_parser, "console-info", help_text

    def _get(self, uri, do_async):
        """GET a resource body, returning {} on any failure.

        :param uri: Redfish resource URI to query.
        :param do_async: run the query asynchronously (subscribes to the event loop).
        :return: the resource body dict, or {} on any failure.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Report each manager's console blocks (capability, not a live stream).

        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: run the per-manager queries asynchronously.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult whose data is a list of console rows
            (Manager, Console, Enabled, ConnectTypes, MaxSessions).
        """
        rows = []
        try:
            manager_ids = self.discover_manager_ids() or []
        except Exception:
            manager_ids = []
        for mgr_uri in manager_ids:
            mdata = self._get(mgr_uri, do_async)
            for kind in ("SerialConsole", "GraphicalConsole", "CommandShell"):
                block = mdata.get(kind)
                if not isinstance(block, dict):
                    continue
                rows.append({
                    "Manager": mgr_uri.rsplit("/", 1)[-1],
                    "Console": kind,
                    "Enabled": block.get("ServiceEnabled"),
                    "ConnectTypes": block.get("ConnectTypesSupported"),
                    "MaxSessions": block.get("MaxConcurrentSessions"),
                })
        return CommandResult(rows, None, None, None)
