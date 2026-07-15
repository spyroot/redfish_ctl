"""iDRAC change boot order and boot options.
It requires server reboot.

python redfish_ctl.py change-boot-order --from_spec specs/change_boot_order_spec.json

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidJsonSpec
from ..cmd_utils import from_json_spec
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, RedfishApiRespond, Singleton
from ..redfish_manager import CommandResult
from ..redfish_shared import RedfishJson


class ChangeBootOrder(
    RedfishManagerBase,
    scm_type=ApiRequestType.ChangeBootOrder,
    name='change_boot_order',
    metaclass=Singleton):
    """Command change boot order.
    """

    def __init__(self, *args, **kwargs):
        super(ChangeBootOrder, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_file_save=False, is_expanded=False, is_reboot=True)
        spec_from_group = cmd_parser.add_argument_group('json', '# JSON from a spec options')

        cmd_parser.add_argument(
            '--boot_order',
            required=False, dest='boot_order',
            default="", type=str,
            help="Coma seperated list. Example HardDisk.List.1-1,NIC.Integrated.1-1-1")

        spec_from_group.add_argument(
            '-s', '--from_spec',
            help="Read json spec for new bios attributes,  "
                 "(Example --from_spec new_bios.json)",
            type=str, required=True, dest="from_spec", metavar="file name",
            default=None
        )

        cmd_parser.add_argument(
            '-c', '--commit', action='store_true',
            required=False, dest="do_commit", default=False,
            help="by default, bios change created in a pending state, "
                 "hence we can cancel, otherwise pass --commit or -c"
        )

        cmd_parser.add_argument(
            '-p', '--commit_pending', action='store_true',
            required=False, default=False,
            help="If idrac already has a  scheduled pending value, "
                 "this option will commit the scheduled change "
                 "and reboot the host.")

        help_text = "command change boot order and boot options"
        return cmd_parser, "change-boot-order", help_text

    def execute(self,
                boot_order: str,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                do_async: Optional[bool] = False,
                from_spec: Optional[str] = "",
                do_reboot: Optional[bool] = False,
                do_commit: Optional[bool] = False,
                commit_pending: Optional[bool] = False,
                **kwargs
                ) -> CommandResult:
        """
        Command change boot order and options, this API uses compute system

        "Boot": {
        "BootOptions": {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/BootOptions"
        },
        "BootOrder": [
            "HardDisk.List.1-1",
            "NIC.Integrated.1-1-1",
            "NIC.Slot.8-1",
            "NIC.Slot.8-1",
            "NIC.Slot.8-1",
            "NIC.Slot.8-1",
            "Optical.iDRACVirtual.1-1"
        ],
        "BootOrder@odata.count": 7,
        "BootSourceOverrideEnabled": "Disabled",
        "BootSourceOverrideMode": "Legacy",
        "BootSourceOverrideTarget": "None",
        "BootSourceOverrideTarget@Redfish.AllowableValues": [
            "None",
            "Pxe",
            "Floppy",
            "Cd",
            "Hdd",
            "BiosSetup",
            "Utilities",
            "UefiTarget",
            "SDCard",
            "UefiHttp"
        ],
        "Certificates": {
            "@odata.id": "/redfish/v1/Systems/System.Embedded.1/Boot/Certificates"
        },
        "UefiTargetBootSourceOverride": null

        :param commit_pending:
        :param do_commit:
        :param do_reboot:
        :param from_spec:
        :param boot_order:
        :param filename:
        :param do_async:
        :param data_type:
        :param kwargs:
        :return: return cmd result
        :raise FailedDiscoverAction
        """

        if from_spec is None or len(from_spec) == 0:
            target_api = f"{self.idrac_manage_servers}/Bios"

            cmd_query = self.base_query(
                target_api, filename=filename,
                do_async=do_async, do_expanded=False
            )
            if cmd_query.error is not None:
                return cmd_query

            current_boot_mode = cmd_query.data[
                RedfishJson.Attributes]['SetBootOrderEn']
            self.logger.info("Current boot mode", current_boot_mode)

            if boot_order is not None:
                boot_order = boot_order.strip().split(",")

            payload = {
                "Boot": {
                    "BootOrder": boot_order
                }
            }
        else:
            # read from spec
            payload = from_json_spec(from_spec)
            if 'Boot' not in payload:
                raise InvalidJsonSpec(
                    "Invalid boot source spec.  Please check example."
                )
            if 'BootOrder' not in payload["Boot"]:
                raise InvalidJsonSpec(
                    "Invalid boot source spec.  Please check example."
                )

        if commit_pending:
            self.logger.info("Committing pending changes.")
            # we commit with a reboot
            _ = self.sync_invoke(
                ApiRequestType.JobApply, "job_apply",
                do_reboot=True, do_watch=True
            )

        target_patch_api = f"{self.idrac_manage_servers}"
        self.logger.info(f"Sending patch request {target_patch_api}.")
        cmd_result, api_resp = self.base_patch(
            target_patch_api, payload=payload,
            do_async=do_async, expected_status=202
        )

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            self.logger.info(f"Fetching task {task_id} state.")
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id
        elif api_resp in (RedfishApiRespond.Success, RedfishApiRespond.Ok):
            if do_commit:
                self.logger.info("Commit changes and rebooting.")
                # we commit with a reboot
                cmd_apply = self.sync_invoke(
                    ApiRequestType.JobApply,
                    "job_apply", do_reboot=True, do_watch=True,
                )
                if cmd_apply.error is not None:
                    return cmd_apply

        if do_reboot:
            self.reboot()

        return cmd_result
