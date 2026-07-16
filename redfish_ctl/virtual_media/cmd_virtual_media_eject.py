"""Virtual media eject command.

Command provides the option to eject  virtual disk from a Redfish endpoint.

Example:
    redfish_ctl eject_vm --device_id 1

Will eject virtual device id 1

Author Mus spyroot@gmail.com
"""
import argparse
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, RedfishApiRespond, Singleton


class VirtualMediaEject(RedfishManagerBase,
                        scm_type=ApiRequestType.VirtualMediaEject,
                        name='virtual_disk_eject',
                        metaclass=Singleton):
    """Eject virtual media over the Redfish API.
    Virtual medial must be inserted, otherwise command throw exception.
    """

    def __init__(self, *args, **kwargs):
        """Initialize the eject_vm command."""
        super(VirtualMediaEject, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args
        :param cls:
        :return:
        """
        cmd_arg = argparse.ArgumentParser(add_help=False)
        cmd_arg.add_argument('--device_id', required=False, type=str,
                             default="1",
                             help="virtual media device id. Example 1 or 2")

        help_text = "command eject the virtual media"
        return cmd_arg, "eject_vm", help_text

    def execute(self,
                device_id: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_strict: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Execute command eject virtual media eject.

        :param do_strict: will raise exception if media already ejected.
                          mainly if caller need have special handler.
        :param device_id: virtual media device id. (1 or 2)
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: will not block and return result as future.
        :param data_type:  json, xml etc.
        :return: named tuple CommandResult
        :raise: InvalidArgument if media already ejected or invalid device id
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        virtual_media = self.sync_invoke(
            ApiRequestType.VirtualMediaGet,
            "virtual_disk_query"
        )
        if virtual_media.error is not None:
            return virtual_media

        members = virtual_media.data['Members']
        actions = [
            self.discover_redfish_actions(self, m) for m
            in members if m['Id'] == device_id
        ]
        if len(actions) == 0:
            valid_dev_id = [m['Id'] for m in members]
            raise InvalidArgument(f"Invalid device id {device_id}, "
                                  f"supported device id {valid_dev_id}")

        if any(m['Id'] == device_id and m.get('Inserted') is False for m in members):
            if do_strict:
                raise InvalidArgument("Image already ejected")
            else:
                return CommandResult(
                    {
                        "Status": RedfishApiRespond.Ok
                     }, None, None, None)

        eject_rest = [a['EjectMedia'].target for a in actions][-1]
        payload = {}
        cmd_result, api_resp = self.base_post(
            eject_rest, payload=payload,
            do_async=do_async, expected_status=202
        )

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            self.logger.info(f"Fetching task {task_id} state.")
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        return cmd_result
