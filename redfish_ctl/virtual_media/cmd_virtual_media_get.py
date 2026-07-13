"""iDRAC command return virtual media.

redfish_ctl get_vm

Command provides the option to retrieve virtual media from iDRAC
and serialize back to caller as JSON, YAML, or XML.
In addition, it automatically registered to the command line ctl tool.
Similarly to the rest command caller can save to a file and
consume asynchronously or synchronously.

redfish_ctl get_vm

- Each command return a result and list of REST Actions.
- Each command loaded based __init__ hence anyone can extend and add custom command.

Example.

w will filter by device_id 1 and status inserted.
get_vm --device_id 1 --filter_key Inserted

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..base_manager import CommandBase
from ..cmd_utils import save_if_needed
from ..command_shared import (
    ApiRequestType,
    Singleton,
)
from ..redfish_manager import CommandResult


class VirtualMediaGet(CommandBase,
                      scm_type=ApiRequestType.VirtualMediaGet,
                      name='virtual_disk_query',
                      metaclass=Singleton):
    """iDRACs REST API Virtual Disk Query Command, fetch virtual disk, caller can save
    result to a file or output stdout or pass downstream to jq etc. tools.
    """
    def __init__(self, *args, **kwargs):
        super(VirtualMediaGet, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args
        :param cls:
        :return:
        """
        cmd_arg = argparse.ArgumentParser(add_help=False)
        cmd_arg.add_argument('-f', '--filename', required=False, type=str,
                             default="",
                             help="filename if we need to save a respond "
                                  "to a file.")

        cmd_arg.add_argument('--device_id', required=False, type=str,
                             default="",
                             help="filter based on device id.")

        cmd_arg.add_argument('--filter_key', required=False, type=str,
                             default="",
                             help="filter based sub-key under device.")

        help_text = "command fetch the virtual media."
        return cmd_arg, "get_vm", help_text

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                device_id: Optional[str] = "",
                filter_key: Optional[str] = "",
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Execute command and fetch virtual media status

        :param device_id: filter based on device
        :param filter_key filter based on key.
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

        # Resolve the VirtualMedia collection from whichever resource exposes it
        # (a Manager on iLO/Supermicro, the ComputerSystem on Dell) — no hardcoded id.
        vm_uri = self.discover_virtual_media_uri()
        r = f"{self._default_method}{self.idrac_ip}{vm_uri}?$expand=*($levels=1)"

        response = self.api_get_call(r, headers)
        self.default_error_handler(response)
        data = response.json()
        if device_id is not None and len(device_id) > 0:
            member_data = data['Members']
            target_device = None
            for e in member_data:
                if 'Id' in e and device_id.strip() == e['Id']:
                    target_device = e
                    break
            if target_device is None:
                return CommandResult(
                    {"Status": f"device id {device_id} not found"}, None, None, None)
            else:
                data = target_device

        if filter_key is not None and len(filter_key) > 0:
            if filter_key.strip() not in data:
                return CommandResult(
                    {
                        "Status": f"key {filter_key} not found"
                    }, None, None, None
                )
            data = data[filter_key]

        save_if_needed(filename, data)
        return CommandResult(data, None, None, None)
