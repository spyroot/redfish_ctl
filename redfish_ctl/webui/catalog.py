"""Curated read-only command catalog for the web explorer.

Each entry maps a human label to a real redfish_ctl command
``(ApiRequestType, name)`` pair — the exact identifiers ``sync_invoke`` dispatches
on. The catalog is a strict allow-list: only read-only commands appear here, so
the explorer can never invoke a mutating action (power, BIOS write, RAID, media,
firmware flash) even if asked. Heavy commands (full sensor/telemetry walks) are
flagged so the UI can warn before a long call.

Grounded in the live registry (``RedfishManagerBase._registry``); every (api, command)
pair below is a real registered command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..redfish_manager_shared import ApiRequestType


@dataclass(frozen=True)
class CommandEntry:
    """One explorable read-only command."""

    label: str
    api: ApiRequestType
    command: str
    description: str
    heavy: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "api": self.api.name,
            "command": self.command,
            "description": self.description,
            "heavy": self.heavy,
        }


# Domain -> ordered read commands. Order is display order in the tree.
CATALOG: tuple[tuple[str, tuple[CommandEntry, ...]], ...] = (
    ("Network", (
        CommandEntry("NIC / DPU firmware", ApiRequestType.NicFirmware, "nic-firmware",
                     "ConnectX NIC + BlueField DPU firmware versions (UpdateService + NetworkAdapters)"),
        CommandEntry("Network adapters", ApiRequestType.NetworkAdapters, "network-adapters",
                     "Physical NIC/DPU cards across all chassis"),
        CommandEntry("Network ports", ApiRequestType.NetworkPorts, "network-ports",
                     "Per-adapter ports (speed, link status)"),
        CommandEntry("Ethernet interfaces", ApiRequestType.EthernetInterfaces, "ethernet-interfaces",
                     "Host/BMC EthernetInterfaces"),
        CommandEntry("NVLink ports", ApiRequestType.NvLinkPorts, "nvlink-ports",
                     "NVLink fabric ports"),
        CommandEntry("BMC network protocol", ApiRequestType.ManagerNetworkProtocol, "manager-network",
                     "Manager NetworkProtocol (NTP/DNS/services state)"),
    )),
    ("Thermal & Power", (
        CommandEntry("Sensors", ApiRequestType.Sensors, "sensors",
                     "All chassis sensor readings", heavy=True),
        CommandEntry("Thermal", ApiRequestType.Thermal, "thermal",
                     "ThermalSubsystem temperatures and fans", heavy=True),
        CommandEntry("Power", ApiRequestType.Power, "power",
                     "PowerSubsystem: supplies, input/output watts"),
        CommandEntry("Environment metrics", ApiRequestType.EnvironmentMetrics, "environment-metrics",
                     "Per-resource energy/power/temperature rollups"),
        CommandEntry("Leak detectors", ApiRequestType.LeakDetectors, "leak-detectors",
                     "Liquid-cooling LeakDetection states"),
        CommandEntry("Power smoothing", ApiRequestType.PowerSmoothing, "power-smoothing",
                     "NVIDIA PowerSmoothing profiles and setpoints"),
    )),
    ("Accelerator & Telemetry", (
        CommandEntry("GPU metrics", ApiRequestType.GpuMetrics, "gpu-metrics",
                     "Per-GPU ProcessorMetrics/MemoryMetrics", heavy=True),
        CommandEntry("Memory metrics", ApiRequestType.MemoryMetrics, "memory-metrics",
                     "MemoryMetrics per module"),
        CommandEntry("Processor metrics", ApiRequestType.ProcessorMetrics, "processor-metrics",
                     "ProcessorMetrics per CPU/GPU"),
        CommandEntry("Metric reports", ApiRequestType.MetricReports, "metric-reports",
                     "TelemetryService MetricReports", heavy=True),
        CommandEntry("Metric definitions", ApiRequestType.MetricReportDefinitions, "metric-definitions",
                     "TelemetryService MetricReportDefinitions"),
    )),
    ("Firmware", (
        CommandEntry("Firmware inventory", ApiRequestType.FirmwareInventoryQuery, "firmware_inv_query",
                     "Full UpdateService/FirmwareInventory (all components)"),
        CommandEntry("Firmware (BMC/BIOS)", ApiRequestType.FirmwareQuery, "firmware_query",
                     "Firmware versions summary"),
        CommandEntry("Update service", ApiRequestType.UpdateServiceQuery, "update_service",
                     "UpdateService capabilities and advertised actions"),
    )),
    ("System", (
        CommandEntry("System", ApiRequestType.SystemQuery, "system_query",
                     "ComputerSystem: power state, health, model"),
        CommandEntry("Chassis", ApiRequestType.ChassisQuery, "chassis_service_query",
                     "Chassis inventory and health"),
        CommandEntry("Manager (BMC)", ApiRequestType.ManagerQuery, "manager_query",
                     "BMC Manager resource"),
        CommandEntry("PCI devices", ApiRequestType.PciDeviceQuery, "pci_device_query",
                     "PCIeDevices inventory"),
        CommandEntry("OEM info", ApiRequestType.OemInfo, "oem-info",
                     "Vendor/OEM identity and extensions"),
        CommandEntry("Capability report", ApiRequestType.CapabilityReport, "capability-report",
                     "Vendor capability profile for this endpoint"),
    )),
    ("BIOS & Boot", (
        CommandEntry("BIOS attributes", ApiRequestType.BiosQuery, "bios_inventory",
                     "Current BIOS attributes"),
        CommandEntry("BIOS pending", ApiRequestType.BiosQueryPending, "bios_query_pending",
                     "Staged (pending) BIOS changes"),
        CommandEntry("Boot", ApiRequestType.BootQuery, "boot_query",
                     "Boot sources / boot order"),
        CommandEntry("Current boot", ApiRequestType.CurrentBoot, "current_boot_query",
                     "Current one-time boot override"),
        CommandEntry("Secure boot", ApiRequestType.SecureBoot, "secure-boot",
                     "SecureBoot enable state"),
    )),
    ("Storage", (
        CommandEntry("Storage list", ApiRequestType.StorageListQuery, "storage_list",
                     "Storage controllers overview"),
        CommandEntry("Volumes", ApiRequestType.VolumeQuery, "vol_query",
                     "Configured volumes"),
        CommandEntry("Drives", ApiRequestType.Drives, "drives_query",
                     "Physical drives"),
    )),
    ("BMC operations (read)", (
        CommandEntry("Manager time", ApiRequestType.ManagerTime, "manager-time",
                     "BMC date/time and timezone"),
        CommandEntry("Event service", ApiRequestType.EventServiceQuery, "event-service",
                     "EventService and subscriptions"),
        CommandEntry("Jobs", ApiRequestType.Jobs, "jobs_sources_query",
                     "Lifecycle jobs / task queue"),
        CommandEntry("Logs", ApiRequestType.Logs, "logs",
                     "Log services (SEL/LC)"),
    )),
)


def catalog_json() -> list[dict[str, Any]]:
    """Return the catalog as JSON-serializable domains for the UI."""
    return [
        {"domain": domain, "commands": [entry.to_dict() for entry in entries]}
        for domain, entries in CATALOG
    ]


def resolve(command: str) -> CommandEntry | None:
    """Return the catalog entry for a command name, or None if not allow-listed."""
    for _domain, entries in CATALOG:
        for entry in entries:
            if entry.command == command:
                return entry
    return None
