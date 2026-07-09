"""iDRAC query command

Command provides capability query
particular account.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgumentFormat
from ..idrac_manager import IDracManager
from ..idrac_shared import IDRAC_API
from ..idrac_shared import IDRAC_JSON
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class QueryAccount(IDracManager,
                   scm_type=ApiRequestType.QueryAccount,
                   name='query_account',
                   metaclass=Singleton):
    """A command query iDRAC resource based on a resource path.
    """

    def __init__(self, *args, **kwargs):
        super(QueryAccount, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--account', required=True, dest="account",
            type=str, default=None,
            help="account id")

        help_text = "command query based on resource."
        return cmd_parser, "account", help_text

    def execute(self,
                account: str,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes query account cmd.

        :param account:  account id
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """

        if account is None or len(account) == 0:
            raise InvalidArgumentFormat("Account is empty string.")

        # lookup by username
        if not account.isnumeric():
            query_result = self.sync_invoke(
                ApiRequestType.QueryAccounts, "query_accounts", is_username_only=True)
            usernames = query_result.data
            accounts_id = [u[IDRAC_JSON.AccountId] for u in usernames
                           if u[IDRAC_JSON.Username].lower() == account.lower()]
            if len(accounts_id) > 0:
                account = accounts_id[-1]

        return self.base_query(f"{IDRAC_API.Account}{account}",
                               filename=filename,
                               do_async=do_async,
                               do_expanded=do_expanded)
