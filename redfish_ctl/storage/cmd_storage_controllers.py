"""Storage controller query command.

Command provides the option to retrieve storage controller information
from a Redfish endpoint and serialize back as caller as JSON, YAML, or XML. In addition,
it automatically registered to the command line ctl tool. Similarly to
the rest command caller can save to a file and consume asynchronously
or synchronously.

On return will hold list of controller in CommandResult.data

['NonRAID.Slot.6-1',
'AHCI.Embedded.1-1',
 'AHCI.Slot.4-1',
 'AHCI.Embedded.2-1']

python redfish_ctl.py storage

    redfish_ctl storage-controllers

Example: filter by controller type
redfish_ctl.py --json storage --filter AHCI

[
    "AHCI.Embedded.1-1",
    "AHCI.Slot.4-1",
    "AHCI.Embedded.2-1"
]

Author Mus spyroot@gmail.com
"""
import argparse
import asyncio
from abc import abstractmethod
from typing import Optional, Tuple

from ..cmd_utils import save_if_needed
from ..idrac_manager import IDracManager
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class StorageQuery(IDracManager, scm_type=ApiRequestType.StorageQuery,
                   name='storage_query',
                   metaclass=Singleton):
    """
    Command return storage controller
    """

    def __init__(self, *args, **kwargs):
        """Initialize the storage_query command."""
        super(StorageQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls) -> Tuple[argparse.ArgumentParser, str, str]:
        """Register command and all command optional flags.
        :param cls:
        :return: tuple ArgumentParser, command name, help_text
        """
        cmd_parser = cls.base_parser()

        cmd_parser.add_argument('-s', '--save_all', required=False, type=str, dest="do_save",
                                default=False,
                                help="for deep walk by default we don't "
                                     "save result to a file. save_all "
                                     "will save to a separate file.")

        cmd_parser.add_argument('--filter', required=False, type=str, dest="id_filter",
                                default="",
                                help="Filter based on controller information. "
                                     "Example --filter AHCI , "
                                     "will filter and only AHCI controllers")

        help_text = "command fetch the storage information"
        return cmd_parser, "storage-controllers", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                do_deep: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                id_filter: Optional[str] = "",
                **kwargs) -> CommandResult:
        """Query storage controllers from a Redfish endpoint.

         Example of members
         "Members": [
                 {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Storage/NonRAID.Slot.6-1"},
                 {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Storage/AHCI.Embedded.1-1"},
                 {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Storage/AHCI.Slot.4-1"},
                 {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Storage/AHCI.Embedded.2-1"}
                 ]
        :param filename:
        :param data_type:
        :param do_deep:
        :param do_expanded:
        :param verbose:
        :param do_async:
        :param id_filter:
        :param kwargs:
        :return:
        """
        if verbose:
            self.logger.debug(f"cmd args data_type: {data_type} "
                  f"do_deep:{do_deep} do_async:{do_async} id_filter:{id_filter}")
            self.logger.debug(f"the rest of args: {kwargs}")

        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        target_api = f"{self.idrac_manage_servers}/Storage"
        if do_expanded:
            r = f"{self._default_method}{self.idrac_ip}{target_api}{self.expanded()}"
        else:
            r = f"{self._default_method}{self.idrac_ip}{target_api}"

        if not do_async:
            response = self.api_get_call(r, headers)
            self.default_error_handler(response)
        else:
            loop = self._event_loop()
            response = loop.run_until_complete(self.api_async_get_until_complete(r, headers))

        data = response.json()
        save_if_needed(filename, data)

        if id_filter is not None and len(id_filter) > 0:
            controller_list = [id_data['@odata.id'].split("/")[-1] for id_data in data['Members']
                               if id_filter in id_data['@odata.id']]

            controller_uri = [id_data['@odata.id'] for id_data in data['Members']
                              if id_filter in id_data['@odata.id']]

        else:
            controller_list = [id_data['@odata.id'].split("/")[-1] for id_data in data['Members']]
            controller_uri = [id_data['@odata.id'] for id_data in data['Members']]

        return CommandResult(controller_list, controller_uri, None, None)
