"""Dell OS deployment driver-pack query action.

    redfish_ctl dell-os-driver-pack
    redfish_ctl dell-os-driver-pack --dry_run

``#DellOSDeploymentService.GetDriverPackInfo`` is a Dell OEM query carried over
POST. The command discovers the DellOSDeploymentService resource from the
ComputerSystem OEM links and returns the advertised driver-pack information.

Author Mus spyroot@gmail.com
"""
import asyncio
import json
from abc import abstractmethod
from typing import Optional

from ..actions.action_policy import classify
from ..redfish_manager import CommandResult
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, RedfishApiRespond, Singleton

_DRIVER_PACK_ACTION = "#DellOSDeploymentService.GetDriverPackInfo"
_LEGACY_OS_DEPLOYMENT_SERVICE = (
    "/redfish/v1/Dell/Systems/System.Embedded.1/DellOSDeploymentService"
)
_STANDARD_OS_DEPLOYMENT_SERVICE = (
    "/redfish/v1/Systems/System.Embedded.1/Oem/Dell/DellOSDeploymentService"
)


class DellOsDeploymentDriverPack(
        RedfishManagerBase,
        scm_type=ApiRequestType.DellOsDeploymentDriverPack,
        name="dell-os-driver-pack",
        metaclass=Singleton):
    """Query Dell OS deployment driver-pack information."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-os-driver-pack command."""
        super(DellOsDeploymentDriverPack, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``dell-os-driver-pack`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the Dell action target without POSTing",
        )
        return (
            cmd_parser,
            "dell-os-driver-pack",
            "command query Dell OS deployment driver-pack info",
        )

    @staticmethod
    def _link(data, *path):
        """Return a nested Redfish ``@odata.id`` link.

        :param data: resource body to inspect.
        :param path: nested mapping keys leading to a link object.
        :return: linked URI, or None when absent.
        """
        node = data or {}
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node.get("@odata.id") if isinstance(node, dict) else None

    def _os_deployment_service_uri(self, do_async):
        """Resolve the DellOSDeploymentService resource URI.

        The XR8620t corpus advertises the service through
        ``ComputerSystem.Links.Oem.Dell.DellOSDeploymentService``. Older
        fixtures used the legacy ``/redfish/v1/Dell/Systems/...`` path, so keep
        that as the final fallback.

        :param do_async: issue the ComputerSystem query over the async path.
        :return: the discovered DellOSDeploymentService URI.
        """
        candidates = []
        try:
            system = self.base_query(self.idrac_manage_servers, do_async=do_async).data
        except Exception:
            system = {}
        link = self._link(
            system,
            "Links",
            "Oem",
            "Dell",
            "DellOSDeploymentService",
        )
        if link:
            candidates.append(link)
        candidates.extend([
            _STANDARD_OS_DEPLOYMENT_SERVICE,
            _LEGACY_OS_DEPLOYMENT_SERVICE,
        ])
        for candidate in candidates:
            try:
                result = self.base_query(candidate, do_async=do_async)
            except Exception:
                continue
            if result.error is None and isinstance(result.data, dict):
                return candidate
        return candidates[0]

    def _resolve_driver_pack_action(self, service_uri, do_async):
        """Read the service resource and resolve the driver-pack action target.

        :param service_uri: DellOSDeploymentService resource URI.
        :param do_async: issue the service query over the async path.
        :return: tuple of (actions, target, error).
        """
        try:
            resource = self.base_query(service_uri, do_async=do_async).data or {}
        except Exception as exc:
            return {}, None, f"failed to read {service_uri}: {exc}"
        actions = self.discover_redfish_actions(self, resource)
        full_targets = self._flatten_action_targets(resource)
        target = full_targets.get(_DRIVER_PACK_ACTION)
        if target is None:
            action = actions.get("GetDriverPackInfo")
            target = getattr(action, "target", None)
        if target is None:
            available = sorted(set(list(actions.keys()) + list(full_targets.keys())))
            return actions, None, {
                "action": _DRIVER_PACK_ACTION,
                "available": available,
            }
        return actions, target, None

    def execute(self,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Resolve and invoke ``#DellOSDeploymentService.GetDriverPackInfo``.

        :param dry_run: resolve the target and show it without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue underlying reads/POSTs on the async path when True.
        :return: CommandResult with driver-pack data, dry-run preview, or an error.
        """
        service_uri = self._os_deployment_service_uri(do_async)
        actions, target, error = self._resolve_driver_pack_action(
            service_uri, do_async
        )
        if error is not None:
            if isinstance(error, dict):
                return CommandResult(
                    error,
                    actions,
                    None,
                    f"action '{_DRIVER_PACK_ACTION}' not found on {service_uri}",
                )
            return CommandResult(None, actions, None, error)

        level = classify(_DRIVER_PACK_ACTION)
        payload = {}
        if dry_run:
            return CommandResult({
                "dry_run": True,
                "action": _DRIVER_PACK_ACTION,
                "target": target,
                "payload": payload,
                "level": level.value,
                "blocked": None,
            }, actions, None, None)

        headers = {}
        if data_type == "json":
            headers.update(self.json_content_type)
        try:
            request_uri = f"{self._default_method}{self.redfish_ip}{target}"
            if do_async:
                loop = asyncio.get_event_loop()
                response, api_resp = loop.run_until_complete(
                    self.api_async_post_until_complete(
                        request_uri,
                        json.dumps(payload),
                        headers,
                        expected=200,
                    )
                )
            else:
                response = self.api_post_call(
                    request_uri,
                    json.dumps(payload),
                    headers,
                )
                api_resp = self.default_post_success(response, expected=200)
        except Exception as exc:
            return CommandResult(
                {"action": _DRIVER_PACK_ACTION, "target": target},
                actions,
                None,
                str(exc),
            )

        if api_resp == RedfishApiRespond.AcceptedTaskGenerated:
            return CommandResult({
                "executed": True,
                "action": _DRIVER_PACK_ACTION,
                "target": target,
                "level": level.value,
                "task_id": self.job_id_from_header(response, strict=False),
            }, actions, None, None)

        try:
            response_data = response.json()
        except ValueError:
            response_data = {}
        return CommandResult({
            "executed": True,
            "action": _DRIVER_PACK_ACTION,
            "target": target,
            "level": level.value,
            "response": response_data,
        }, actions, None, None)
