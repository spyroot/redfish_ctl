"""Dell JobService CreateRebootJob action command.

Examples::

    redfish_ctl dell-job-reboot
    redfish_ctl dell-job-reboot --reboot-job-type GracefulReboot
    redfish_ctl dell-job-reboot --reboot-job-type PowerCycle --confirm

The command discovers the Dell JobService resource from Manager resources, then
discovers the CreateRebootJob action target from that resource. It previews by
default and only POSTs when ``--confirm`` is supplied.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton


_CREATE_REBOOT_ACTION = "#DellJobService.CreateRebootJob"
_CREATE_REBOOT_NAME = "CreateRebootJob"
_DEFAULT_SERVICE = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService"


class DellJobReboot(RedfishManagerBase,
                    scm_type=ApiRequestType.DellJobReboot,
                    name="dell-job-reboot",
                    metaclass=Singleton):
    """Preview or create a Dell OEM reboot job."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-job-reboot command."""
        super(DellJobReboot, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-job-reboot`` subcommand.

        :param cls: command class used to build the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--reboot-job-type",
            dest="reboot_job_type",
            choices=["PowerCycle", "GracefulReboot", "ForcedReboot"],
            default=None,
            help="Dell RebootJobType to preview or create; omit to list targets",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DellJobService URI when more than one target is found",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="create the reboot job instead of previewing the POST",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and payload without POSTing",
        )
        return cmd_parser, "dell-job-reboot", "command create Dell reboot jobs"

    @staticmethod
    def _link(data, key):
        """Return an ``@odata.id`` link value from a Redfish object.

        :param data: Redfish resource body.
        :param key: link property name.
        :return: linked URI, or None when absent or malformed.
        """
        link = data.get(key) if isinstance(data, dict) else None
        return link.get("@odata.id") if isinstance(link, dict) else None

    @staticmethod
    def _dell(data):
        """Return the ``Oem.Dell`` extension block from a resource.

        :param data: Redfish resource body.
        :return: Dell OEM block, or an empty dict.
        """
        oem = data.get("Oem") if isinstance(data, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body, tolerating missing optional resources.

        :param uri: Redfish resource URI.
        :param do_async: run the query asynchronously when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _service_uris(self, do_async):
        """Return candidate DellJobService resource URIs.

        :param do_async: run manager queries asynchronously when True.
        :return: list of unique candidate DellJobService resource URIs.
        """
        uris = []
        for manager_uri in self.discover_manager_ids() or []:
            manager = self._get(manager_uri, do_async)
            dell = self._dell(manager)
            service_uri = self._link(dell, "DellJobService")
            if not service_uri:
                service_uri = f"{manager_uri.rstrip('/')}/Oem/Dell/DellJobService"
            if service_uri not in uris:
                uris.append(service_uri)
        if not uris:
            uris.append(_DEFAULT_SERVICE)
        return uris

    @staticmethod
    def _allowable_values(service):
        """Return advertised RebootJobType values from a DellJobService body.

        :param service: parsed DellJobService resource.
        :return: list of allowed RebootJobType values.
        """
        actions = service.get("Actions") if isinstance(service, dict) else None
        action = actions.get(_CREATE_REBOOT_ACTION) if isinstance(actions, dict) else None
        values = (
            action.get("RebootJobType@Redfish.AllowableValues")
            if isinstance(action, dict)
            else None
        )
        return list(values) if isinstance(values, list) else []

    def _discover_rows(self, do_async):
        """Discover DellJobService resources advertising CreateRebootJob.

        :param do_async: run underlying queries asynchronously when True.
        :return: list of target rows.
        """
        rows = []
        for service_uri in self._service_uris(do_async):
            service = self._get(service_uri, do_async)
            targets = self._flatten_action_targets(service)
            target = targets.get(_CREATE_REBOOT_ACTION)
            if not target:
                continue
            rows.append({
                "Action": _CREATE_REBOOT_ACTION,
                "Resource": service_uri,
                "Target": target,
                "AllowableRebootJobTypes": self._allowable_values(service),
            })
        return rows

    @staticmethod
    def _matches(rows, resource_uri):
        """Filter discovered rows by optional resource URI.

        :param rows: discovered DellJobService target rows.
        :param resource_uri: optional DellJobService URI selector.
        :return: matching rows.
        """
        if not resource_uri:
            return list(rows)
        normalized = resource_uri.rstrip("/")
        return [row for row in rows if row["Resource"].rstrip("/") == normalized]

    def execute(self,
                reboot_job_type: Optional[str] = None,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or create a Dell OEM reboot job.

        :param reboot_job_type: Dell ``RebootJobType`` value to send.
        :param resource_uri: optional DellJobService URI to disambiguate targets.
        :param confirm: create the reboot job when True.
        :param dry_run: force preview mode even when ``confirm`` is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        rows = self._discover_rows(bool(do_async))
        if reboot_job_type is None:
            return CommandResult({"dell_job_reboot_targets": rows}, None, None, None)

        matches = self._matches(rows, resource_uri)
        if not matches:
            missing = resource_uri or reboot_job_type
            return CommandResult(
                {"available": rows},
                None,
                None,
                f"DellJobService CreateRebootJob target not found: {missing}",
            )
        if len(matches) > 1:
            return CommandResult(
                {"matches": matches},
                None,
                None,
                "multiple DellJobService CreateRebootJob targets found; pass --resource-uri",
            )

        row = matches[0]
        result = self.invoke_action(
            row["Resource"],
            _CREATE_REBOOT_NAME,
            payload={"RebootJobType": reboot_job_type},
            full_action_type=_CREATE_REBOOT_ACTION,
            do_async=do_async,
            dry_run=bool(dry_run) or not bool(confirm),
            confirm=bool(confirm),
        )
        if not confirm and isinstance(result.data, dict):
            result.data["requires_confirm"] = True
            result.data["blocked"] = "DellJobService CreateRebootJob requires --confirm"
        return result
