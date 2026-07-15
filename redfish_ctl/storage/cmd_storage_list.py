"""Storage controller list command.

Command provides the option to retrieve list of storage controllers.

Example expanded
python redfish_ctl.py storage-list -e

    redfish_ctl storage-list -e

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class StorageListView(RedfishManagerBase,
                      scm_type=ApiRequestType.StorageListQuery,
                      name='storage_list',
                      metaclass=Singleton):
    """Fetch the storage controller list over the Redfish API.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the storage_list command."""
        super(StorageListView, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        help_text = "command fetch the storage devices"
        return cmd_parser, "storage-list", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Queries for storage controller list.
        :param do_expanded:
        :param do_async: will not block and return result as future.
        :param filename: if filename indicate call will save the response to this file.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :return: named tuple CommandResult
        :raise: AuthenticationFailed, UnexpectedResponse
        """
        # Standard Redfish Storage subpath; degrade gracefully when a host does
        # not expose it (return empty rather than raising a 404).
        target_api = f"{self.idrac_manage_servers}/Storage"
        try:
            return self.base_query(target_api,
                                   filename=filename,
                                   do_async=do_async,
                                   do_expanded=do_expanded)
        except Exception:
            return CommandResult({}, None, None,
                                 "Storage collection is not available on this host")
