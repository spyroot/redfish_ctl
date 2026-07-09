"""iDRAC query chassis services

Command provides option to query boot source for
pending values.

Author Mus spyroot@gmail.com
"""
import json
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidJsonSpec
from ..cmd_utils import from_json_spec
from ..idrac_shared import IdracApiRespond
from ..redfish_shared import RedfishJson
from ..cmd_utils import str2bool
from ..idrac_shared import IdracApiRespond, ResetType
from ..cmd_utils import save_if_needed
from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import IdracApiRespond, Singleton, ApiRequestType
from ..redfish_manager import CommandResult
from ..idrac_shared import IDRAC_API
from ..idrac_shared import IdracApiRespond


class BootSourceUpdate(IDracManager,
                       scm_type=ApiRequestType.BootSourceUpdate,
                       name='update',
                       metaclass=Singleton):
    """A command query dell OEM for boot source pending changes.
    """

    def __init__(self, *args, **kwargs):
        super(BootSourceUpdate, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_reboot=True)
        mt_group = cmd_parser.add_argument_group('maintenance', '# Maintenance related options')
        spec_from_group = cmd_parser.add_argument_group('json', '# JSON from a spec options')

        cmd_parser.add_argument(
            'apply',
            help='''choose when to apply a change.
                    maintenance will create a future task,
                    on-reset will apply on next rest.
                    ''',
            choices=['on-reset', 'auto-boot', 'maintenance'],
            default="on-reset")

        mt_group.add_argument(
            "--start_date",
            help='''The start of maintenance window format YYYY-MM-DD
                    This will create a future task.
                    ''',
            required=False,
            dest="start_date", metavar="start date",
            type=str
        )

        mt_group.add_argument(
            "--start_time",
            help='''The start of maintenance window format HH:MM:SS
                    This will create a future task.
                    ''',
            required=False, default="00:00:00",
            dest="start_time", metavar="start time",
            type=str
        )

        mt_group.add_argument(
            '--duration',
            help="maximum duration for a maintenance window from a start time",
            type=int, required=False,
            dest="default_duration",
            metavar="duration",
            default=600
        )

        cmd_parser.add_argument(
            '--show', action='store_true',
            required=False, dest="do_show", default=False,
            help="will only show and will not apply any changes.."
        )

        cmd_parser.add_argument(
            '-c', '--commit', action='store_true',
            required=False, dest="do_commit", default=False,
            help="by default bios change created in pending state, "
                 "hence we can cancel, otherwise pass --commit or -c"
        )

        cmd_parser.add_argument(
            '--commit_pending', action='store_true',
            required=False, default=False,
            help="if idrac already container pending value, this option "
                 "will commit change and reboot host")

        spec_from_group.add_argument(
            '-s', '--from_spec',
            help="Read json spec for new bios attributes,  "
                 "(Example --from_spec new_bios.json)",
            type=str, required=True, dest="from_spec", metavar="file name",
            default=None
        )

        help_text = "command updates boot sources"
        return cmd_parser, "boot-source-update", help_text

    def execute(self,
                apply: Optional[str] = "on-reset",
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_reboot: Optional[bool] = False,
                do_commit: Optional[bool] = False,
                do_show: Optional[bool] = False,
                data_filter: Optional[str] = None,
                from_spec: Optional[str] = "",
                start_date: Optional[str] = "",
                start_time: Optional[str] = "",
                default_duration: Optional[int] = 600,
                **kwargs) -> CommandResult:
        """Executes and update boot sources.

        :param do_show:
        :param do_commit:
        :param default_duration:
        :param start_time:
        :param start_date:
        :param apply:
        :param from_spec:
        :param do_reboot: will reboot a host.
        :param data_filter: filter applied to find specific device.
        :param do_async: note async will subscribe to an event loop.
        :param filename: if filename indicate call will save a bios setting to a file.
        :param verbose: enables verbose output
        :param data_type: json or xml
        :return: CommandResult and if filename provide will save to a file.
        """
        target_api = f"{self.idrac_manage_servers}/Oem/Dell/DellBootSources/Settings"

        # we read either from a file or form args
        # comma seperated.
        payload = {}
        try:
            if from_spec is not None and len(from_spec) > 0:
                payload = from_json_spec(from_spec)
                if len(payload) == 0:
                    return CommandResult(
                        {
                            "Status": "Empty bios spec."
                        },
                        None, None, None
                    )
        except json.decoder.JSONDecodeError as jde:
            raise InvalidJsonSpec(
                "It looks like your JSON spec is invalid. "
                "JSONlint the file and check..".format(str(jde)))

        job_req_payload = self.create_apply_time_req(
            apply, start_time, start_date, default_duration
        )
        payload.update(job_req_payload)
        if verbose:
            self.logger.info(f"payload: {payload}")

        if do_show:
            return CommandResult(payload, None, None, None)

        self.logger.info(f"Sending patch request {target_api}.")
        cmd_result, api_resp = self.base_patch(
            target_api, payload=payload,
            do_async=do_async, expected_status=202
        )

        if api_resp == IdracApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
            self.logger.info(f"Fetching task {task_id} state.")
            task_state = self.fetch_task(task_id)
            cmd_result.data['task_state'] = task_state
            cmd_result.data['task_id'] = task_id
        elif api_resp.Success or api_resp.Ok:
            if do_commit:
                self.logger.info(f"Commit changes and rebooting.")
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
