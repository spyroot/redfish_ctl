"""Account query command.

Command query account.

    redfish_ctl accounts

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import REDFISH_API, REDFISH_JSON, ApiRequestType, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishJson


class QueryAccounts(IDracManager,
                    scm_type=ApiRequestType.QueryAccounts,
                    name='query_accounts',
                    metaclass=Singleton):
    """Query a Redfish endpoint resource by resource path.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the query_accounts command."""
        super(QueryAccounts, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_async=True)
        help_text = "command query accounts."
        cmd_parser.add_argument(
            '--usernames', action='store_true', required=False, dest="is_username_only",
            help="Filter and only output usernames ")

        return cmd_parser, "accounts", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                is_username_only: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes query command
        python redfish_ctl.py

        :param is_username_only:  filter and only output username,
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save the response to this file.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :return: CommandResult and if filename provide will save to a file.
        """
        is_expanded = False
        if is_username_only or do_expanded:
            is_expanded = True

        cmd_result = self.base_query(REDFISH_API.Accounts,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=is_expanded)

        if is_username_only and RedfishJson.Members in cmd_result.data:
            accounts_data = cmd_result.data
            members = accounts_data[RedfishJson.Members]
            usernames = [
                {
                    REDFISH_JSON.Username: m[REDFISH_JSON.Username],
                    REDFISH_JSON.AccountId: m[REDFISH_JSON.AccountId]
                }
                for m in members
                if isinstance(m, dict) and REDFISH_JSON.Username in m and len(m[REDFISH_JSON.Username]) > 0]

            cmd_result = CommandResult(usernames, None, None, None)

        return cmd_result
