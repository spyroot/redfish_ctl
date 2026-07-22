"""Preview or re-initiate Dell Lifecycle Controller auto-discovery.

    redfish_ctl dell-lc-autodiscovery
    redfish_ctl dell-lc-autodiscovery --perform NextBoot
    redfish_ctl dell-lc-autodiscovery --mode dhs --perform Off --confirm

The command resolves ``#DellLCService.ReInitiateAutoDiscovery`` or
``#DellLCService.ReInitiateDHS`` from the Dell Lifecycle Controller service.
Re-initiating discovery changes BMC provisioning behavior, so the command lists
targets with no ``--perform`` value and otherwise previews by default.
"""
from abc import abstractmethod
from typing import Optional

from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult

_SERVICE_NAME = "DellLCService"
_DEFAULT_SERVICE_URI = (
    "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellLCService"
)
_LEGACY_SERVICE_URI = (
    "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DellLCService"
)

_ACTION_SPECS = {
    "auto-discovery": {
        "short": "ReInitiateAutoDiscovery",
        "full": "#DellLCService.ReInitiateAutoDiscovery",
    },
    "dhs": {
        "short": "ReInitiateDHS",
        "full": "#DellLCService.ReInitiateDHS",
    },
}


class DellLcAutoDiscovery(IDracManager,
                          scm_type=ApiRequestType.DellLcAutoDiscovery,
                          name="dell-lc-autodiscovery",
                          metaclass=Singleton):
    """List or invoke Dell LC auto-discovery actions."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-lc-autodiscovery command."""
        super(DellLcAutoDiscovery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-lc-autodiscovery`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--mode",
            choices=tuple(_ACTION_SPECS),
            default="auto-discovery",
            help="Dell LC action family to run",
        )
        cmd_parser.add_argument(
            "--perform",
            dest="perform_auto_discovery",
            default=None,
            help="PerformAutoDiscovery value such as NextBoot, Now, or Off; "
                 "omit to list discovered action targets",
        )
        cmd_parser.add_argument(
            "--resource-uri",
            dest="resource_uri",
            default=None,
            help="specific DellLCService URI when discovery needs override",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="POST the selected action instead of previewing it",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-lc-autodiscovery",
            "command manage Dell LC auto-discovery action requests",
        )

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
    def _dell_oem_links(manager):
        """Return the Dell block under ``Manager.Links.Oem``.

        :param manager: Redfish Manager resource body.
        :return: Dell OEM links dict, or an empty dict.
        """
        links = manager.get("Links") if isinstance(manager, dict) else None
        oem = links.get("Oem") if isinstance(links, dict) else None
        dell = oem.get("Dell") if isinstance(oem, dict) else None
        return dell if isinstance(dell, dict) else {}

    def _get(self, uri, do_async):
        """GET a Redfish resource body, tolerating optional-resource misses.

        :param uri: Redfish resource URI.
        :param do_async: issue the query on the async path when True.
        :return: parsed resource body, or an empty dict when the read fails.
        """
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _service_uris(self, do_async):
        """Return candidate DellLCService URIs in discovery-first order.

        :param do_async: issue Manager queries on the async path when True.
        :return: de-duplicated candidate service URIs.
        """
        uris = []
        try:
            manager_uris = self.discover_manager_ids() or []
        except Exception:
            manager_uris = []
        for manager_uri in manager_uris:
            manager = self._get(manager_uri, do_async)
            service_uri = self._link(
                self._dell_oem_links(manager),
                _SERVICE_NAME,
            )
            if service_uri:
                uris.append(service_uri)
            uris.append(manager_uri.rstrip("/") + f"/Oem/Dell/{_SERVICE_NAME}")
        uris.extend([_DEFAULT_SERVICE_URI, _LEGACY_SERVICE_URI])
        return list(dict.fromkeys(uri for uri in uris if uri))

    @staticmethod
    def _action_summary(actions, action_name):
        """Return target and inline allowed values for one discovered action.

        :param actions: mapping returned by ``discover_redfish_actions``.
        :param action_name: short Redfish action name.
        :return: dict with target and allowed ``PerformAutoDiscovery`` values.
        """
        action = actions.get(action_name)
        args = getattr(action, "args", {}) or {}
        allowed = sorted(args.get("PerformAutoDiscovery") or [])
        return {
            "target": getattr(action, "target", None),
            "allowed_perform_auto_discovery": allowed,
        }

    def _metadata(self, do_async, resource_uri=None):
        """Return discovered auto-discovery action metadata.

        :param do_async: issue Redfish reads on the async path when True.
        :param resource_uri: optional direct DellLCService URI.
        :return: CommandResult containing target metadata or an error.
        """
        checked = []
        uris = [resource_uri] if resource_uri else self._service_uris(do_async)
        for uri in dict.fromkeys(uri for uri in uris if uri):
            service = self._get(uri, do_async)
            if not service:
                checked.append(uri)
                continue
            actions = self.discover_redfish_actions(self, service)
            full_targets = self._flatten_action_targets(service)
            discovered = {}
            for mode, spec in _ACTION_SPECS.items():
                if spec["full"] in full_targets:
                    discovered[mode] = self._action_summary(
                        actions,
                        spec["short"],
                    )
                    discovered[mode]["action"] = spec["full"]
            if discovered:
                return CommandResult(
                    {
                        "lc_service": uri,
                        "actions": discovered,
                    },
                    actions,
                    None,
                    None,
                )
            checked.append(uri)
        wanted = ", ".join(spec["full"] for spec in _ACTION_SPECS.values())
        return CommandResult(
            {"actions": sorted(_ACTION_SPECS), "checked": checked},
            None,
            None,
            f"actions not found: {wanted}",
        )

    def execute(self,
                mode: Optional[str] = "auto-discovery",
                perform_auto_discovery: Optional[str] = None,
                resource_uri: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List or invoke DellLCService auto-discovery actions.

        :param mode: ``auto-discovery`` or ``dhs``.
        :param perform_auto_discovery: desired ``PerformAutoDiscovery`` value.
        :param resource_uri: optional DellLCService URI to inspect directly.
        :param confirm: POST the action when True.
        :param dry_run: force preview mode even when ``confirm`` is True.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue Redfish reads/POST on the async path when True.
        :return: CommandResult with a listing, preview, execution result, or error.
        """
        metadata = self._metadata(bool(do_async), resource_uri)
        if metadata.error is not None or perform_auto_discovery is None:
            return metadata

        action_mode = mode or "auto-discovery"
        spec = _ACTION_SPECS.get(action_mode)
        if spec is None:
            valid = ", ".join(sorted(_ACTION_SPECS))
            return CommandResult(
                {"mode": action_mode, "valid_modes": sorted(_ACTION_SPECS)},
                None,
                None,
                f"invalid mode '{action_mode}'; expected one of: {valid}",
            )

        result = self.invoke_action(
            metadata.data["lc_service"],
            spec["short"],
            payload={"PerformAutoDiscovery": perform_auto_discovery},
            full_action_type=spec["full"],
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
        if result.error is None and isinstance(result.data, dict):
            result.data.setdefault("lc_service", metadata.data["lc_service"])
            result.data.setdefault("mode", action_mode)
        return result
