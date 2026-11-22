"""Change BIOS settings command.

Command provides capability to change bios settings
from JSON spec or via command line via --attr_name and comma separate list
of BIOS options and --attr_value.

Example:

redfish_ctl bios-change --from_spec ./test.spec.json --show on-reset

{
        "Attributes": {
                "MemFrequency": "MaxPerf",
                        "MemTest": "Disabled",
                        "OsWatchdogTimer": "Disabled",
                        "ProcCStates": "Disabled",
                        "SriovGlobalEnable": "Enabled"
        }
}

Author Mus spyroot@gmail.com
"""
import json
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import (
    InvalidArgument,
    InvalidJsonSpec,
    UncommittedPendingChanges,
    UnsupportedAction,
)
from ..cmd_utils import from_json_spec
from ..redfish_api_common import (
    REDFISH_API,
    REDFISH_JSON,
    ApiRequestType,
    RedfishApiRespond,
    Singleton,
)
from ..redfish_manager import CommandResult, RedfishManager
from ..redfish_shared import RedfishApi, RedfishJson
from .bios_registry import _query_bios_registry, _query_bios_resource


class BiosChangeSettings(RedfishManager,
                         scm_type=ApiRequestType.BiosChangeSettings,
                         name='bios_change_settings',
                         metaclass=Singleton):
    """A command changes bios settings
    """

    def __init__(self, *args, **kwargs):
        """Construct the bios-change command, forwarding credentials to the base manager."""
        super(BiosChangeSettings, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register command and all optional flags.
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser(is_reboot=True, is_expanded=False)

        bios_group = cmd_parser.add_argument_group('bios', '# BIOS attribute options')
        mt_group = cmd_parser.add_argument_group('maintenance', '# Maintenance related options')
        spec_from_group = cmd_parser.add_argument_group('json', '# JSON from a spec options')

        bios_group.add_argument(
            '--attr_name',
            help="attribute name or list. Example --attr_name MemTest,EmbSata",
            type=str, required=False, dest="attr_name", metavar="attribute name",
            default=None
        )

        bios_group.add_argument(
            '--attr_value',
            help="attribute name or list. Example --attr_values Disabled,RaidMode",
            type=str, required=False, dest="attr_value", metavar="attribute value",
            default=None
        )

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
            help='''
            The start of maintenance window format HH:MM:SS \n
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

        help_text = "command change bios configuration attributes"
        return cmd_parser, "bios-change", help_text

    @property
    def ok(self):
        """Accepted; a Task has been generated.

        :return: HTTP 202 status code.
        """
        return 202

    @property
    def accepted(self):
        """Accepted; a Task has been generated.

        :return: HTTP 202 status code.
        """
        return 202

    @property
    def success(self):
        """Success, but no response data
        :return:
        """
        return 204

    @staticmethod
    def crete_bios_config(current_config, attr_name, attr_val) -> dict:
        """Create new config for a bios.
        :param current_config:
        :param attr_name: bios attribute name
        :param attr_val: bios attribute value
        :return: a dict
        """
        bios_payload = {
            "Attributes": {
            }
        }

        attribute_names = []
        attribute_values = []

        if attr_name is not None and len(attr_name) > 0:
            if "," in attr_name:
                attribute_names = attr_name.split(",")
            else:
                attribute_names = [attr_name]

        if attr_val is not None and len(attr_val) > 0:
            if "," in attr_val:
                attribute_values = attr_val.split(",")
            else:
                attribute_values = [attr_val]

        if len(attribute_names) != len(attribute_values):
            raise InvalidArgument("Number of attribute "
                                  "and values mismatched.")

        for name, val in zip(attribute_names, attribute_values):
            bios_payload["Attributes"][name.strip()] = val.strip()

        for k, v in bios_payload[REDFISH_JSON.Attributes].items():
            for current_data in current_config:
                if k in current_data.values() and current_data['Type'] == "Integer":
                    bios_payload[REDFISH_JSON.Attributes][k] = int(v)

        return bios_payload

    def _resolve_bios_settings_uri(self, do_async: Optional[bool]) -> str:
        """Find the SettingsObject advertised by the BIOS resource.

        :param do_async: whether to use the asynchronous GET transport.
        :return: the validated settings path, or the legacy path on Dell.
        :raises UnsupportedAction: when no usable settings path is available.
        """
        system_uri = self.managed_system_uri
        if not system_uri:
            raise UnsupportedAction("No managed system is available for BIOS settings.")

        bios = _query_bios_resource(self, f"{system_uri}{RedfishApi.Bios}", do_async).data
        settings = bios.get("@Redfish.Settings") if isinstance(bios, dict) else None
        settings_object = settings.get("SettingsObject") if isinstance(settings, dict) else None
        link = settings_object.get(RedfishJson.Data_id) if isinstance(settings_object, dict) else None
        uri = self._local_resource_uri(link)
        if uri:
            return uri
        if link is None and self.uses_dell_job_semantics:
            return f"{system_uri}{REDFISH_API.BiosSettings}"
        raise UnsupportedAction("BIOS does not advertise a valid SettingsObject URI.")

    def execute(self,
                attr_name: Optional[str] = None,
                attr_value: Optional[str] = None,
                apply: Optional[str] = "on-reset",
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_reboot: Optional[bool] = False,
                do_show: Optional[bool] = False,
                do_commit: Optional[bool] = False,
                commit_pending: Optional[bool] = False,
                start_date: Optional[str] = "",
                start_time: Optional[str] = "",
                from_spec: Optional[str] = "",
                default_duration: Optional[int] = 600,
                **kwargs) -> CommandResult:
        """Executes command to change bios settings.

        :param do_reboot:
        :param default_duration:
        :param apply:
        :param do_show: will only show final spec but will not apply any changes.
        :param do_commit: apply staged settings using the Dell BIOS job.
        :param attr_value:  attribute value or list of values
        :param attr_name: attribute name or list of values
        :param do_async: note async will subscribe to an event loop.
        :param filename: if filename indicate call will save the response to this file.
        :param from_spec: read bios changes from a spec file
        :param commit_pending: apply existing pending settings using the Dell BIOS job.
        :param verbose: enables verbose output
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param start_time:
        :param start_date:
        :return: CommandResult and if filename provide will save to a file.
        :raises UnsupportedAction: when the requested commit or staging target is unavailable.
        """
        if not do_show and (do_commit or commit_pending) and not self.uses_dell_job_semantics:
            raise UnsupportedAction("--commit and --commit_pending require Dell BIOS job support.")

        # We read changes either from a JSON spec file or build them from
        # --attr_name/--attr_value. A spec file is self-contained, so the BIOS
        # registry is only fetched (and validated) on the args path.
        try:
            if from_spec is not None and len(from_spec) > 0:
                payload = from_json_spec(from_spec)
            else:
                cmd_result = _query_bios_registry(self, do_async, filename=filename)
                if cmd_result.error is not None:
                    return cmd_result
                if verbose:
                    self.default_json_printer(cmd_result.data)

                if not isinstance(cmd_result.data, dict):
                    return CommandResult(
                        {"Status": "Failed fetch bios registry"}, None, None, None
                    )
                registry = cmd_result.data.get(REDFISH_JSON.RegistryEntries)
                if not isinstance(registry, dict) \
                        or REDFISH_JSON.Attributes not in registry:
                    return CommandResult(
                        {"Status": "Failed fetch attributes from bios registry"},
                        None, None, None
                    )
                attribute_data = registry[REDFISH_JSON.Attributes]
                if not isinstance(attribute_data, list):
                    return CommandResult(
                        {"Status": "Bios registry attributes are malformed"},
                        None, None, None
                    )
                payload = self.crete_bios_config(
                    attribute_data, attr_name, attr_value
                )
            if len(payload) == 0:
                return CommandResult(
                    {"Status": "Empty bios spec."}, None, None, None
                )
        except json.decoder.JSONDecodeError as jde:
            raise InvalidJsonSpec(
                f"It looks like your JSON spec is invalid. "
                f"JSONlint the file and check: {jde}"
            )

        job_req_payload = self.create_apply_time_req(
            apply, start_date, start_time, default_duration)
        payload.update(job_req_payload)
        if verbose:
            self.logger.info(f"payload: {payload}")

        if do_show:
            return CommandResult(payload, None, None, None)

        target_api = self._resolve_bios_settings_uri(do_async)

        if self.uses_dell_job_semantics:
            cmd_pending = self.sync_invoke(
                ApiRequestType.BiosQueryPending,
                "bios_query_pending",
            )

            if len(cmd_pending.data) > 0:
                if commit_pending:
                    cmd_apply = self.sync_invoke(
                        ApiRequestType.JobApply,
                        "job_apply", do_reboot=True, do_watch=True, setting="bios",
                    )
                    print("Applied change", cmd_apply.data)
                else:
                    raise UncommittedPendingChanges(
                        "BIOS contains uncommitted pending changes in the queue. "
                        "Please apply changes first."
                    )

        cmd_result, api_resp = self.base_patch(
            target_api, payload=payload,
            do_async=do_async
        )
        if cmd_result.error is not None:
            return cmd_result

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            task_id = cmd_result.data['task_id']
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
