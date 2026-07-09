"""iDRAC reset a chassis power state.

The Chassis schema represents the physical components of a system.
This resource represents the sheet-metal confined spaces
and logical zones such as racks, enclosures, chassis and all other
containers. Subsystems, such as sensors, that operate outside a system's
data plane are linked either directly or indirectly through this resource.
A subsystem that operates outside a system's data plane are not
accessible to software that runs on the system.

 idrac_ctl chassis-update

Author Mus spyroot@gmail.com
"""
import json
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..cmd_exceptions import InvalidArgumentFormat
from ..cmd_exceptions import InvalidJsonSpec
from ..cmd_utils import from_json_spec
from ..idrac_manager import IDracManager
from ..idrac_shared import IDRAC_API
from ..idrac_shared import Singleton, ApiRequestType
from ..redfish_manager import CommandResult


class ChassisUpdate(IDracManager,
                    scm_type=ApiRequestType.ChassisUpdate,
                    name='update_chassis',
                    metaclass=Singleton):
    """
    This  action update chassis .
    """
    def __init__(self, *args, **kwargs):
        super(ChassisUpdate, self).__init__(*args, **kwargs)

    @property
    def accepted(self):
        """Accepted; a Task has been generated"""
        return 202

    @property
    def success(self):
        """Success, but no response data
        :return:
        """
        return 204

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_file_save=False, is_expanded=False, is_reboot=True)
        chassis_group = cmd_parser.add_argument_group('chassis', '# chassis related options')
        spec_from_group = cmd_parser.add_argument_group('json', '# JSON from a spec options')

        chassis_group.add_argument(
            '--chassis_id', required=True, dest='chassis_id',
            default=None, type=str, help="chassis id.")

        spec_from_group.add_argument(
            '-s', '--from_spec',
            help="Read json spec for new bios attributes,  "
                 "(Example --from_spec chassis.json)",
            type=str, required=True, dest="from_spec", metavar="file name",
            default=None
        )

        help_text = "command update chassis"
        return cmd_parser, "chassis-update", help_text

    def execute(self,
                chassis_id,
                from_spec: Optional[str] = "",
                do_async: Optional[bool] = False,
                data_type: Optional[str] = "json",
                do_reboot: Optional[bool] = False,
                **kwargs
                ) -> CommandResult:
        """
        Update chassis from spec file.

        :param do_reboot: will reboot a chassis
        :param chassis_id of the property of the Chassis resource
        :param from_spec:  a path to json spec file
        :param do_async: optional for asyncio
        :param data_type: a data type
        :param kwargs: the rest args
        :return: return cmd result
        :raise InvalidJsonSpec if file provided can not parse by json decoder
        :raise InvalidArgumentFormat if json spec emtpy
        """
        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)

        if chassis_id is None or len(chassis_id) == 0:
            raise InvalidArgumentFormat(
                f"chassis_id is empty string"
            )

        try:
            if from_spec is None or len(from_spec) > 0:
                raise InvalidArgument("Invalid from_spec")
        except json.decoder.JSONDecodeError as jde:
            raise InvalidJsonSpec(
                "It looks like your JSON spec is invalid. "
                "JSONlint the file and check..".format(str(jde)))

        payload = from_json_spec(from_spec)
        if len(payload) == 0:
            raise InvalidArgumentFormat(
                f"Check check {from_spec} it looks like empty spec."
            )

        r = f"{IDRAC_API.Chassis}/{chassis_id}"

        cmd_result, api_resp = self.base_patch(
            r, payload=payload, do_async=do_async,
            data_type=data_type
        )

        if api_resp.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            task_state = self.fetch_task(cmd_result.data['task_id'])
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id

        if do_reboot:
            self.reboot(do_watch=False if do_async else True)

        return cmd_result
