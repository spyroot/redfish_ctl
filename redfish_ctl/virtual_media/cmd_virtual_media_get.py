"""Return virtual media.

redfish_ctl get_vm

Command provides the option to retrieve virtual media from a Redfish endpoint
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

from ..cmd_exceptions import ResourceNotFound
from ..cmd_utils import save_if_needed
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishJson


class VirtualMediaGet(RedfishManagerBase,
                      scm_type=ApiRequestType.VirtualMediaGet,
                      name='virtual_disk_query',
                      metaclass=Singleton):
    """Virtual media query command, fetch virtual media, caller can save
    result to a file or output stdout or pass downstream to jq etc. tools.
    """
    def __init__(self, *args, **kwargs):
        """Initialize the get_vm command."""
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
        :param filter_key: filter based on key.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: will not block and return result as future.
        :param filename: if filename indicate call will save the response to this file.
        :param data_type:  json, xml etc.
        :return: named tuple CommandResult
        :raise: AuthenticationFailed, UnexpectedResponse
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        # Resolve the VirtualMedia collection from whichever resource exposes it
        # (a Manager on iLO/Supermicro, the ComputerSystem on Dell) — no hardcoded id.
        try:
            vm_uri = self.discover_virtual_media_uri()
        except ResourceNotFound as exc:
            status = str(exc)
            return CommandResult({"Status": status}, None, None, status)
        r = f"{self._default_method}{self.idrac_ip}{vm_uri}?$expand=*($levels=1)"

        response = self.api_get_call(r, headers)
        if response.status_code == 501:
            status = "standard VirtualMedia endpoint is not implemented on this BMC"
            return CommandResult(
                {
                    "error": status,
                    "status_code": 501,
                    "target": vm_uri,
                    "suggested_command": "vm-mount --status",
                },
                None,
                None,
                status,
            )
        self.default_error_handler(response)
        data = response.json()
        data["Members"] = self._hydrate_member_links(
            data.get("Members"), do_async=do_async
        )
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

    def _hydrate_member_links(self, members, do_async: Optional[bool] = False):
        """Fetch linked VirtualMedia members when the service did not expand them.

        :param members: the Members value from the collection; returned unchanged
            when it is not a list.
        :param do_async: fetch each linked member via an asynchronous query.
        :return: the members with any unexpanded links resolved to their detail dicts.
        """
        if not isinstance(members, list):
            return members
        hydrated = []
        for member in members:
            if not isinstance(member, dict):
                hydrated.append(member)
                continue
            if "Id" in member and "Actions" in member:
                hydrated.append(member)
                continue
            uri = member.get(RedfishJson.Data_id)
            if not isinstance(uri, str):
                hydrated.append(member)
                continue
            try:
                detail = self.base_query(uri, do_async=do_async).data
            except Exception:
                hydrated.append(member)
                continue
            hydrated.append(detail if isinstance(detail, dict) else member)
        return hydrated
