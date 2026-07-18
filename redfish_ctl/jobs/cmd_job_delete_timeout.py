"""Set Dell JobService delete-on-completion timeout.

    redfish_ctl job-delete-timeout
    redfish_ctl job-delete-timeout --minutes 2880
    redfish_ctl job-delete-timeout --minutes 2880 --confirm

The command discovers DellJobService from the Manager OEM Dell links and invokes
``#DellJobService.SetDeleteOnCompletionTimeout``. It lists the discovered target
when no timeout is supplied, and it previews by default when a timeout is given.
Use ``--confirm`` to POST the new timeout.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_DELL_JOB_TIMEOUT_ACTION = "#DellJobService.SetDeleteOnCompletionTimeout"
_DELL_JOB_SERVICE_FALLBACK = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/DellJobService"
)


class JobDeleteTimeout(RedfishManagerBase,
                       scm_type=ApiRequestType.JobDeleteTimeout,
                       name="job-delete-timeout",
                       metaclass=Singleton):
    """Set DellJobService automatic completed-job cleanup timeout."""

    def __init__(self, *args, **kwargs):
        """Initialize the job-delete-timeout command."""
        super(JobDeleteTimeout, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``job-delete-timeout`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--minutes",
            required=False,
            dest="minutes",
            type=int,
            default=None,
            help="DeleteOnCompletionTimeoutMinutes value to set; omit to list "
                 "the discovered DellJobService action target",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the SetDeleteOnCompletionTimeout POST; without it the "
                 "command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show the payload without POSTing; "
                 "overrides --confirm",
        )
        return (
            cmd_parser,
            "job-delete-timeout",
            "command set Dell JobService completed-job cleanup timeout",
        )

    @staticmethod
    def _link(data, key):
        """Return a Redfish link target from a ``{key: {@odata.id}}`` property.

        :param data: resource body containing the link.
        :param key: property name whose ``@odata.id`` to extract.
        :return: the linked URI, or None when absent or malformed.
        """
        link = (data or {}).get(key)
        return link.get("@odata.id") if isinstance(link, dict) else None

    @classmethod
    def _dell_oem_link(cls, manager, key):
        """Return an OEM Dell link from a Manager resource.

        :param manager: Manager resource body.
        :param key: OEM Dell link name, such as ``DellJobService``.
        :return: the linked URI, or None when absent.
        """
        links = (manager or {}).get("Links", {})
        if not isinstance(links, dict):
            return None
        oem_links = links.get("Oem", {})
        if not isinstance(oem_links, dict):
            return None
        dell_links = oem_links.get("Dell", {})
        if not isinstance(dell_links, dict):
            return None
        return cls._link(dell_links, key)

    def _job_service_uri(self, do_async):
        """Resolve the DellJobService URI from Manager OEM links.

        :param do_async: issue Manager queries over the async Redfish path.
        :return: discovered DellJobService URI, or the legacy Dell fallback.
        """
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            try:
                manager = self.base_query(
                    manager_uri.rstrip("/"),
                    do_async=do_async,
                ).data or {}
            except Exception:
                continue
            target = self._dell_oem_link(manager, "DellJobService")
            if target:
                return target
        return _DELL_JOB_SERVICE_FALLBACK

    def _job_service(self, do_async):
        """Read the DellJobService resource.

        :param do_async: issue the query over the async Redfish path.
        :return: tuple of ``(uri, body)`` or ``(uri, CommandResult)`` on error.
        """
        uri = self._job_service_uri(do_async)
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            return uri, CommandResult(None, None, None, f"failed to read {uri}: {exc}")
        return uri, data

    def _timeout_metadata(self, do_async):
        """Return discovered timeout action metadata.

        :param do_async: issue the DellJobService query over the async Redfish path.
        :return: CommandResult with service and action target metadata.
        """
        uri, service = self._job_service(do_async)
        if isinstance(service, CommandResult):
            return service
        actions = self.discover_redfish_actions(self, service)
        target = self._flatten_action_targets(service).get(_DELL_JOB_TIMEOUT_ACTION)
        if target is None:
            available = sorted(set(list(actions.keys())
                                   + list(self._flatten_action_targets(service).keys())))
            return CommandResult(
                {
                    "job_service": uri,
                    "action": _DELL_JOB_TIMEOUT_ACTION,
                    "available": available,
                },
                actions,
                None,
                f"action '{_DELL_JOB_TIMEOUT_ACTION}' not found on {uri}",
            )
        return CommandResult(
            {
                "job_service": uri,
                "action": _DELL_JOB_TIMEOUT_ACTION,
                "target": target,
                "current_minutes": service.get("DeleteOnCompletionTimeoutMinutes"),
                "current_jobs": service.get("CurrentNumberOfJobs"),
                "maximum_jobs": service.get("MaximumNumberOfJobs"),
            },
            actions,
            None,
            None,
        )

    @staticmethod
    def _payload(minutes):
        """Build a SetDeleteOnCompletionTimeout payload.

        :param minutes: timeout value in minutes.
        :return: JSON-serializable action payload.
        :raises InvalidArgument: when minutes is negative.
        """
        if minutes is None:
            return None
        if minutes < 0:
            raise InvalidArgument("minutes must be zero or greater")
        return {"DeleteOnCompletionTimeoutMinutes": minutes}

    def execute(self,
                minutes: Optional[int] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or set DellJobService completed-job cleanup timeout.

        :param minutes: timeout value in minutes; None lists target metadata.
        :param confirm: authorize the timeout POST to actually fire.
        :param dry_run: resolve the target and show the payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying query and POST on the async path.
        :return: a CommandResult with target metadata, dry-run preview, or POST result.
        """
        payload = self._payload(minutes)
        if payload is None:
            return self._timeout_metadata(do_async)

        return self.invoke_action(
            self._job_service_uri(do_async),
            "SetDeleteOnCompletionTimeout",
            payload=payload,
            full_action_type=_DELL_JOB_TIMEOUT_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
