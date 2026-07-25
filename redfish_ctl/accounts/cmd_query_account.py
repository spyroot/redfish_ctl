"""Account query command.

Command provides capability query
particular account.

    redfish_ctl account --account <id>

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgumentFormat
from ..idrac_manager import IDracManager
from ..idrac_shared import REDFISH_API, REDFISH_JSON, ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class QueryAccount(IDracManager,
                   scm_type=ApiRequestType.QueryAccount,
                   name='query_account',
                   metaclass=Singleton):
    """Query a Redfish endpoint resource by resource path.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the query_account command."""
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
        :param filename: if filename indicate call will save the response to this file.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :return: CommandResult and if filename provide will save to a file.
        """

        if account is None or len(account) == 0:
            raise InvalidArgumentFormat("Account is empty string.")

        # lookup by username
        if not account.isnumeric():
            query_result = self.sync_invoke(
                ApiRequestType.QueryAccounts, "query_accounts", is_username_only=True)
            usernames = query_result.data
            accounts_id = [u[REDFISH_JSON.AccountId] for u in usernames
                           if u[REDFISH_JSON.Username].lower() == account.lower()]
            if len(accounts_id) > 0:
                account = accounts_id[-1]

        return self.base_query(f"{REDFISH_API.Account}{account}",
                               filename=filename,
                               do_async=do_async,
                               do_expanded=do_expanded)
