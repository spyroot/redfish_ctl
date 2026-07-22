"""Change Dell vFlash state through DellPersistentStorageService.

    redfish_ctl dell-vflash-state
    redfish_ctl dell-vflash-state --requested-state Enable --dry_run
    redfish_ctl dell-vflash-state --requested-state Disable --confirm

The command discovers ``#DellPersistentStorageService.VFlashStateChange`` from
the Manager OEM Dell persistent-storage link. With no requested state it lists
the discovered target and advertised states. Changing vFlash state rewrites BMC
configuration, so the action previews by default and only POSTs with
``--confirm``.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..redfish_manager import CommandResult
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_shared import RedfishApi

_VFLASH_STATE_ACTION = "#DellPersistentStorageService.VFlashStateChange"
_PERSISTENT_STORAGE_FALLBACK = (
    f"{RedfishApi.Version}/Managers/iDRAC.Embedded.1/Oem/Dell/"
    "DellPersistentStorageService"
)


class DellVFlashStateChange(IDracManager,
                            scm_type=ApiRequestType.DellVFlashStateChange,
                            name="dell-vflash-state",
                            metaclass=Singleton):
    """Discover and change Dell vFlash state."""

    def __init__(self, *args, **kwargs):
        """Initialize the dell-vflash-state command."""
        super(DellVFlashStateChange, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the guarded ``dell-vflash-state`` subcommand.

        :param cls: command class supplying the shared base parser.
        :return: tuple of (ArgumentParser, command name, command help).
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            "--requested-state",
            required=False,
            dest="requested_state",
            type=str,
            default=None,
            help="Dell vFlash state to request, such as Enable or Disable; "
                 "omit to list the discovered action target",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm",
            default=False,
            help="fire the VFlashStateChange POST; without it the command previews",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="resolve the target and payload without POSTing; overrides --confirm",
        )
        return (
            cmd_parser,
            "dell-vflash-state",
            "command change Dell vFlash state",
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
        :param key: OEM Dell link name, such as ``DellPersistentStorageService``.
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

    def _persistent_storage_uri(self, do_async):
        """Resolve DellPersistentStorageService from Manager OEM links.

        :param do_async: issue Manager queries over the async Redfish path.
        :return: discovered persistent-storage service URI, or the legacy fallback.
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
            target = self._dell_oem_link(manager, "DellPersistentStorageService")
            if target:
                return target
        return _PERSISTENT_STORAGE_FALLBACK

    def _persistent_storage_service(self, do_async):
        """Read the DellPersistentStorageService resource.

        :param do_async: issue the query over the async Redfish path.
        :return: tuple of ``(uri, body)`` or ``(uri, CommandResult)`` on error.
        """
        uri = self._persistent_storage_uri(do_async)
        try:
            data = self.base_query(uri, do_async=do_async).data or {}
        except Exception as exc:
            return uri, CommandResult(None, None, None, f"failed to read {uri}: {exc}")
        return uri, data

    def _state_metadata(self, do_async):
        """Return discovered VFlashStateChange target metadata.

        :param do_async: issue the persistent-storage query over the async Redfish path.
        :return: CommandResult with service, action target, and advertised states.
        """
        uri, service = self._persistent_storage_service(do_async)
        if isinstance(service, CommandResult):
            return service
        actions = self.discover_redfish_actions(self, service)
        action = actions.get("VFlashStateChange")
        requested_states = sorted((getattr(action, "args", {}) or {}).get(
            "RequestedState", []
        ))
        target = self._flatten_action_targets(service).get(_VFLASH_STATE_ACTION)
        if target is None:
            available = sorted(set(list(actions.keys())
                                   + list(self._flatten_action_targets(service).keys())))
            return CommandResult(
                {
                    "persistent_storage_service": uri,
                    "action": _VFLASH_STATE_ACTION,
                    "available": available,
                },
                actions,
                None,
                f"action '{_VFLASH_STATE_ACTION}' not found on {uri}",
            )
        return CommandResult(
            {
                "persistent_storage_service": uri,
                "action": _VFLASH_STATE_ACTION,
                "target": target,
                "requested_states": requested_states,
            },
            actions,
            None,
            None,
        )

    @staticmethod
    def _payload(requested_state):
        """Build a VFlashStateChange payload.

        :param requested_state: requested Dell vFlash state.
        :return: JSON-serializable action payload.
        :raises InvalidArgument: when ``requested_state`` is empty.
        """
        state = (requested_state or "").strip()
        if not state:
            raise InvalidArgument("requested state cannot be empty")
        return {"RequestedState": state}

    def execute(self,
                requested_state: Optional[str] = None,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """List the Dell vFlash target, or change the requested state.

        With no ``--requested-state`` the command returns discovered
        VFlashStateChange metadata without mutating. With a requested state it
        resolves and invokes the action; because this rewrites BMC
        configuration, the POST only fires with ``--confirm``. ``--dry_run``
        remains a no-POST override even when ``--confirm`` is also set.

        :param requested_state: requested Dell vFlash state; None lists target
            metadata.
        :param confirm: authorize the VFlashStateChange POST to actually fire.
        :param dry_run: resolve the target and payload without POSTing.
        :param filename: accepted for CLI compatibility; not used by this command.
        :param data_type: accepted for CLI compatibility; not used by this command.
        :param verbose: accepted for CLI compatibility; not used by this command.
        :param do_async: issue the underlying queries/POST on the async path.
        :return: a CommandResult with target metadata, dry-run preview, or POST
            result.
        :raises InvalidArgument: when ``requested_state`` is empty after trimming.
        """
        if requested_state is None:
            return self._state_metadata(do_async)

        return self.invoke_action(
            self._persistent_storage_uri(do_async),
            "VFlashStateChange",
            payload=self._payload(requested_state),
            full_action_type=_VFLASH_STATE_ACTION,
            do_async=do_async,
            expected_status=202,
            dry_run=bool(dry_run),
            confirm=bool(confirm),
        )
