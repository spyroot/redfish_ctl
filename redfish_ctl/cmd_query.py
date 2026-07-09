"""iDRAC query command

Command provides capability raw query based URI resource,
in case specific action might not implement yet; hence it
is easy to query.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from .idrac_manager import IDracManager
from .idrac_shared import Singleton, ApiRequestType
from .redfish_manager import CommandResult


class QueryIDRAC(
    IDracManager,
    scm_type=ApiRequestType.QueryIdrac,
    name='query_idrac',
    metaclass=Singleton):
    """A command query iDRAC resource based on a resource path.
    """

    def __init__(self, *args, **kwargs):
        super(QueryIDRAC, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '-r', '--resource', required=True, dest="resource",
            type=str, default=None,
            help="Job id. Example /redfish/v1/Managers")
        help_text = "command query based on resource."
        return cmd_parser, "query", help_text

    def execute(self, resource: str,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Executes query command
        python idrac_ctl.py query

        :param resource: path to a resource
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded:  will do expand query
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        return self.base_query(resource,
                               filename=filename,
                               do_async=do_async,
                               do_expanded=do_expanded)
