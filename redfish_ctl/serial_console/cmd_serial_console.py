"""Report or enable the host serial console together with BMC Serial-over-LAN.

    idrac_ctl serial-console                       # report current state
    idrac_ctl serial-console --enable --confirm    # enable BIOS redirection + SOL

An out-of-band serial console needs two coupled settings:

1. the host BIOS must redirect its console to a serial device, and
2. the BMC Serial-over-LAN (SOL) service must be enabled so that device is reachable.

This command reads both, and with ``--enable`` it PATCHes the discovered serial BIOS
attribute into the system's pending ``Bios/Settings`` and enables each manager's
``SerialConsole`` service, so SOL reaches the same device the BIOS redirects to.

Vendor-neutral: the system/manager ids and the BIOS attribute are DISCOVERED, never
hardcoded. Known enable values cover Dell (``SerialComm`` -> ``OnConRedirCom2``) and
Supermicro / NVIDIA Grace (``SerialPortConfiguration`` -> ``ConsoleEnabledSbsa``);
on any other box pass ``--bios_attr``/``--bios_value``.

Mutating: ``--enable`` previews the resolved targets + payloads and writes NOTHING
unless ``--confirm`` is given. BIOS changes take effect on the next host reboot.

Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_exceptions import InvalidArgument
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult

# Host serial-console BIOS attribute -> the value that ENABLES redirection.
# Keyed by attribute name so it resolves whatever the vendor calls itself.
_SERIAL_BIOS_ENABLE = {
    "SerialComm": "OnConRedirCom2",                    # Dell iDRAC (redirect to COM2)
    "SerialPortConfiguration": "ConsoleEnabledSbsa",   # Supermicro / NVIDIA Grace (SBSA UART)
}


class SerialConsoleConfig(IDracManager,
                          scm_type=ApiRequestType.SerialConsoleConfig,
                          name='serial-console',
                          metaclass=Singleton):
    """Report or enable host BIOS serial redirection together with BMC SOL."""

    def __init__(self, *args, **kwargs):
        super(SerialConsoleConfig, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``serial-console`` subcommand."""
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument(
            '--enable', action='store_true', dest='enable', default=False,
            help="enable BIOS serial redirection AND the BMC SOL service.")
        cmd_parser.add_argument(
            '--confirm', action='store_true', dest='confirm', default=False,
            help="actually apply; without it --enable only previews (dry-run).")
        cmd_parser.add_argument(
            '--bios_attr', type=str, dest='bios_attr', default=None, metavar='NAME',
            help="serial BIOS attribute to set (auto-discovered if omitted).")
        cmd_parser.add_argument(
            '--bios_value', type=str, dest='bios_value', default=None, metavar='VALUE',
            help="value that enables redirection (default: known per attribute).")
        help_text = "report or enable host serial redirection + BMC Serial-over-LAN"
        return cmd_parser, "serial-console", help_text

    def _get(self, uri, do_async):
        """GET a resource body, returning {} on any failure (read is best-effort)."""
        try:
            return self.base_query(uri, do_async=do_async).data or {}
        except Exception:
            return {}

    @staticmethod
    def _resolve_bios_attr(bios_attrs: dict, override_attr: Optional[str]) -> Optional[str]:
        """Pick the serial-console BIOS attribute present on this box.

        Prefers an explicit override, then a known attribute name, then any
        attribute that looks like a serial-console knob. Returns ``None`` when the
        BIOS exposes nothing recognizable.
        """
        if override_attr:
            return override_attr
        for name in _SERIAL_BIOS_ENABLE:
            if name in bios_attrs:
                return name
        for name in bios_attrs:
            low = name.lower()
            if "serial" in low and any(t in low for t in ("comm", "console", "port", "redir")):
                return name
        return None

    def execute(self,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                enable: Optional[bool] = False,
                confirm: Optional[bool] = False,
                bios_attr: Optional[str] = None,
                bios_value: Optional[str] = None,
                **kwargs) -> CommandResult:
        """Report serial-console/SOL state; with ``enable`` set both (guarded)."""
        system_uri = self.idrac_manage_servers
        bios = self._get(f"{system_uri}/Bios", do_async)
        bios_attrs = bios.get("Attributes", {}) if isinstance(bios, dict) else {}

        attr = self._resolve_bios_attr(bios_attrs, bios_attr)
        current_value = bios_attrs.get(attr) if attr else None
        target_value = bios_value or (_SERIAL_BIOS_ENABLE.get(attr) if attr else None)

        try:
            manager_ids = self.discover_manager_ids() or []
        except Exception:
            manager_ids = []
        sol = []
        for mgr_uri in manager_ids:
            block = self._get(mgr_uri, do_async).get("SerialConsole")
            sol.append({
                "Manager": mgr_uri.rsplit("/", 1)[-1],
                "uri": mgr_uri,
                "ServiceEnabled": block.get("ServiceEnabled") if isinstance(block, dict) else None,
            })

        status = {
            "System": system_uri.rsplit("/", 1)[-1],
            "BiosSerialAttribute": attr,
            "BiosSerialCurrent": current_value,
            "BiosSerialTarget": target_value,
            "Sol": [{"Manager": s["Manager"], "ServiceEnabled": s["ServiceEnabled"]} for s in sol],
        }

        if not enable:
            return CommandResult(status, None, None, None)

        if attr is None:
            candidates = [k for k in bios_attrs if "serial" in k.lower() or "console" in k.lower()]
            raise InvalidArgument(
                "could not discover a serial BIOS attribute; pass --bios_attr "
                f"(candidates: {candidates})")
        if target_value is None:
            raise InvalidArgument(
                f"no known enable value for BIOS attribute {attr!r}; pass --bios_value")

        bios_target = f"{system_uri}/Bios/Settings"
        bios_payload = {"Attributes": {attr: target_value}}
        sol_targets = [
            {"target": s["uri"], "payload": {"SerialConsole": {"ServiceEnabled": True}}}
            for s in sol if s["ServiceEnabled"] is not True
        ]
        plan = {
            "bios": {"target": bios_target, "payload": bios_payload},
            "sol": sol_targets,
            "sol_already_enabled": [s["Manager"] for s in sol if s["ServiceEnabled"] is True],
        }

        if not confirm:
            return CommandResult(
                {"dry_run": True,
                 "note": "preview only; re-run with --confirm to apply. "
                         "BIOS change applies on next reboot.",
                 "plan": plan, "status": status},
                None, None, None)

        applied = {"bios": None, "sol": []}
        bios_result, _ = self.base_patch(bios_target, payload=bios_payload, do_async=do_async)
        applied["bios"] = bios_result.data
        for t in sol_targets:
            sol_result, _ = self.base_patch(t["target"], payload=t["payload"], do_async=do_async)
            applied["sol"].append({"target": t["target"], "result": sol_result.data})

        return CommandResult(
            {"applied": applied, "plan": plan, "status_before": status}, None, None, None)
