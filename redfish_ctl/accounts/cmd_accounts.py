"""iDRAC query command

Command query account.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import IDRAC_API
from ..idrac_shared import IDRAC_JSON
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishJson


class QueryAccounts(IDracManager,
                    scm_type=ApiRequestType.QueryAccounts,
                    name='query_accounts',
                    metaclass=Singleton):
    """A command query iDRAC resource based on a resource path.
    """

    def __init__(self, *args, **kwargs):
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
        python idrac_ctl.py

        :param is_username_only:  filter and only output username,
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        is_expanded = False
        if is_username_only or do_expanded:
            is_expanded = True

        cmd_result = self.base_query(IDRAC_API.Accounts,
                                     filename=filename,
                                     do_async=do_async,
                                     do_expanded=is_expanded)

        if is_username_only and RedfishJson.Members in cmd_result.data:
            accounts_data = cmd_result.data
            members = accounts_data[RedfishJson.Members]
            usernames = [
                {
                    IDRAC_JSON.Username: m[IDRAC_JSON.Username],
                    IDRAC_JSON.AccountId: m[IDRAC_JSON.AccountId]
                }
                for m in members
                if isinstance(m, dict) and IDRAC_JSON.Username in m and len(m[IDRAC_JSON.Username]) > 0]

            cmd_result = CommandResult(usernames, None, None, None)

        return cmd_result
