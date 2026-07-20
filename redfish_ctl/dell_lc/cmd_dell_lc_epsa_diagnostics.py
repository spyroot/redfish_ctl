"""Run Dell Lifecycle Controller ePSA diagnostics through DellLCService.

    redfish_ctl dell-lc-epsa-diagnostics
    redfish_ctl dell-lc-epsa-diagnostics --run-mode Express --reboot-job-type PowerCycle
    redfish_ctl dell-lc-epsa-diagnostics --confirm --i-understand-reboot

``#DellLCService.RunePSADiagnostics`` can schedule host reboot or power-cycle
behavior. The command therefore previews by default, validates the advertised
payload values, and only POSTs when both ``--confirm`` and
``--i-understand-reboot`` are supplied.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_EPSA_ACTION = "#DellLCService.RunePSADiagnostics"


class DellLcEpsaDiagnostics(RedfishManagerBase,
                            scm_type=ApiRequestType.DellLcEpsaDiagnostics,
                            name="dell-lc-epsa-diagnostics",
                            metaclass=Singleton):
    """Run Dell LC ePSA diagnostics through the discovered DellLCService."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-lc-epsa-diagnostics command."""
        super(DellLcEpsaDiagnostics, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-lc-epsa-diagnostics`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--run-mode",
            "--run_mode",
            required=False,
            dest="run_mode",
            type=str,
            default="Express",
            help="RunMode value to send, default: Express",
        )
        cmd_parser.add_argument(
            "--reboot-job-type",
            "--reboot_job_type",
            required=False,
            dest="reboot_job_type",
            type=str,
            default="GracefulRebootWithoutForcedShutdown",
            help="RebootJobType value to send, default: GracefulRebootWithoutForcedShutdown",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the RunePSADiagnostics POST; without it the command previews",
        )
        cmd_parser.add_argument(
            "--i-understand-reboot",
            action="store_true",
            dest="confirm_reboot",
            default=False,
            help="acknowledge that the diagnostics action can reboot or power-cycle the host",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and show it without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-lc-epsa-diagnostics",
            "command run Dell LC ePSA diagnostics",
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

    @staticmethod
    def _oem_dell_link(data, key):
        """Return a link from ``Oem.Dell.<key>`` when it is present.

        :param data: resource body containing an optional Dell OEM link block.
        :param key: Dell OEM link key to resolve.
        :return: the linked URI, or None when absent or malformed.
        """
        oem = (data or {}).get("Oem")
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return DellLcEpsaDiagnostics._link(dell, key) if isinstance(dell, dict) else None

    @staticmethod
    def _members(data):
        """Return collection member ``@odata.id`` strings from a Redfish body.

        :param data: a Redfish collection body.
        :return: list of member URIs.
        """
        if not isinstance(data, dict):
            return []
        return [
            member["@odata.id"] for member in data.get("Members", [])
            if isinstance(member, dict) and isinstance(member.get("@odata.id"), str)
        ]

    def _get(self, uri, do_async):
        """GET a resource body, returning ``{}`` when discovery cannot read it.

        :param uri: Redfish resource URI to fetch.
        :param do_async: issue the query on the async path when True.
        :return: parsed response body, or {} on read failure.
        """
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    def _discover_lc_services(self, do_async):
        """Find DellLCService resources exposing the ePSA diagnostics action.

        The modern Dell corpus links the service from ``Manager.Oem.Dell``.
        Older fixtures expose the legacy ``/redfish/v1/Dell/Managers/...``
        layout, so both shapes are probed.

        :param do_async: issue the underlying reads on the async path when True.
        :return: list of ``{"Id": <id>, "uri": <service uri>}`` dictionaries.
        """
        candidates = []
        for manager_uri in self._members(self._get(f"{RedfishApi.Version}/Managers", do_async)):
            manager = self._get(manager_uri, do_async)
            linked = self._oem_dell_link(manager, "DellLCService")
            if linked:
                candidates.append(linked)
            manager_id = manager.get("Id") if isinstance(manager, dict) else None
            if manager_id:
                candidates.append(f"{RedfishApi.Version}/Dell/Managers/{manager_id}/DellLCService")

        if not candidates:
            candidates.append(
                f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
            )

        services = []
        seen = set()
        for service_uri in candidates:
            if service_uri in seen:
                continue
            service = self._get(service_uri, do_async)
            actions = service.get("Actions") if isinstance(service, dict) else None
            if not isinstance(actions, dict) or _EPSA_ACTION not in actions:
                continue
            seen.add(service_uri)
            services.append({
                "Id": service.get("Id") or service_uri.rsplit("/", 1)[-1],
                "uri": service_uri,
            })
        return services

    @staticmethod
    def _payload(run_mode, reboot_job_type):
        """Build the RunePSADiagnostics action payload.

        :param run_mode: ePSA ``RunMode`` value.
        :param reboot_job_type: ePSA ``RebootJobType`` value.
        :return: JSON payload for the action.
        :raises InvalidArgument: when a required value is blank.
        """
        run_mode = (run_mode or "").strip()
        reboot_job_type = (reboot_job_type or "").strip()
        if not run_mode:
            raise InvalidArgument("run mode cannot be empty")
        if not reboot_job_type:
            raise InvalidArgument("reboot job type cannot be empty")
        return {
            "RunMode": run_mode,
            "RebootJobType": reboot_job_type,
        }

    def execute(self,
                run_mode: Optional[str] = "Express",
                reboot_job_type: Optional[str] = "GracefulRebootWithoutForcedShutdown",
                confirm: Optional[bool] = False,
                confirm_reboot: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List/preview or run Dell LC ePSA diagnostics.

        The command resolves a DellLCService that advertises
        ``#DellLCService.RunePSADiagnostics``. It never POSTs by default; actual
        execution requires ``--confirm`` plus ``--i-understand-reboot`` because
        the advertised ``RebootJobType`` values include host-disrupting modes.

        :param run_mode: Dell ePSA ``RunMode`` payload value.
        :param reboot_job_type: Dell ePSA ``RebootJobType`` payload value.
        :param confirm: authorize the diagnostic action POST.
        :param confirm_reboot: extra acknowledgement for reboot/power-cycle risk.
        :param dry_run: force a preview even when confirmation flags are set.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue reads/POST on the async path when True.
        :return: CommandResult with discovered services, dry-run preview, or
            execution result.
        :raises InvalidArgument: when no capable service is discovered or a
            required payload value is blank.
        """
        services = self._discover_lc_services(bool(do_async))
        if not services:
            raise InvalidArgument(
                "no DellLCService exposing #DellLCService.RunePSADiagnostics found"
            )

        result = self.invoke_action(
            services[0]["uri"],
            "RunePSADiagnostics",
            payload=self._payload(run_mode, reboot_job_type),
            full_action_type=_EPSA_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run) or not (bool(confirm) and bool(confirm_reboot)),
            confirm=bool(confirm),
        )
        if (
                confirm
                and not confirm_reboot
                and not dry_run
                and result.error is None
                and isinstance(result.data, dict)):
            result.data["blocked"] = (
                "ePSA diagnostics requires --confirm and --i-understand-reboot"
            )
        return result
