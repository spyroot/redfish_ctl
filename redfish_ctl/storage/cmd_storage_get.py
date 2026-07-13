"""iDRAC storage cmd

Command provides the option to retrieve storage information for specific
controller disk from iDRAC.

Example
python redfish_ctl.py storage-get --storage_controller NonRAID.Slot.6-1

Expanded
python redfish_ctl.py storage-get --storage_controller NonRAID.Slot.6-1 -e

Filter by OEM
python redfish_ctl.py storage-get -c AHCI.Embedded.2-1 --filter Oem

Filter by Drives and Volumes
python redfish_ctl.py storage-get -c AHCI.Embedded.2-1 --filter Drives,Volumes

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class StorageView(CommandBase,
                  scm_type=ApiRequestType.StorageViewQuery,
                  name='storage_get',
                  metaclass=Singleton):
    """iDRACs REST API fetch storage information.
    """

    def __init__(self, *args, **kwargs):
        super(StorageView, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('-c', '--controller', required=False, type=str,
                                default="",
                                help="controller name.")

        cmd_parser.add_argument('--filter', required=False,
                                type=str, dest="data_filter",
                                default="",
                                help="Filter Controllers/Drives/Volumes. "
                                     "(Example filter both Driver "
                                     "and Volumes -c AHCI.Embedded.2-1 "
                                     "--filter Drives,Volumes")

        help_text = "command fetch the storage information"
        return cmd_parser, "storage-get", help_text

    @staticmethod
    def filter_by_keys(json_data, attr_filter: Optional[str]):
        """Filter attribute from json_data
        :param json_data:
        :param attr_filter:
        :return:
        """
        result = {}
        attr_filter = attr_filter.strip()
        if "," in attr_filter:
            attr_filters = attr_filter.split(",")
        else:
            attr_filters = [attr_filter]

        if len(attr_filters) > 0:
            json_data = dict((k, json_data[k])
                             for k in json_data for a in attr_filters
                             if a.lower() in k.lower())
            result.update(json_data)
        return result

    def execute(self,
                controller: Optional[str] = None,
                data_filter: Optional[str] = None,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Get storage controller details.
        :param data_filter:
        :param do_expanded:
        :param controller: if empty cmd will return list of controllers.
        :param verbose: enables verbose output
        :param do_async: will not block and return result as future.
        :param filename: if filename indicate call will save a bios setting to a file.
        :param data_type:  json, xml etc.
        :return: named tuple CommandResult
        :raise: AuthenticationFailed, UnexpectedResponse
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        target_api = f"{self.idrac_manage_servers}/Storage/{controller}"
        cmd_rest = self.base_query(target_api,
                                   filename=filename,
                                   do_async=do_async,
                                   do_expanded=do_expanded)
        data = cmd_rest.data
        if data_filter is not None and len(data_filter) > 0:
            data = self.filter_by_keys(cmd_rest.data, data_filter)

        return CommandResult(data, None, None, None)
