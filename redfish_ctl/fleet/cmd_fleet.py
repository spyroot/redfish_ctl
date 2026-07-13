"""Read a small fleet inventory through existing redfish_ctl commands."""

from __future__ import annotations

import argparse
import os
from abc import abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..api import RedfishApiError, get_sensors, get_system, get_thermal
from ..base_manager import CommandBase
from ..command_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


@dataclass(frozen=True)
class FleetNode:
    """Connection details for one BMC inventory entry."""

    name: str
    address: str
    username: str
    password: str
    port: int
    insecure: bool
    use_http: bool


def _env_value(raw: Mapping[str, Any], field: str, env_field: str, default: str) -> str:
    value = raw.get(field)
    if value is not None:
        return str(value)
    env_name = raw.get(env_field)
    if env_name:
        return os.environ.get(str(env_name), default)
    return default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "yes", "true", "on"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _inventory_nodes(data: Any) -> list[Mapping[str, Any]]:
    if isinstance(data, list):
        rows = data
    elif isinstance(data, Mapping):
        rows = data.get("nodes", [])
    else:
        rows = []
    return [row for row in rows if isinstance(row, Mapping)]


def load_inventory(path: str | Path) -> tuple[FleetNode, ...]:
    """Load a YAML fleet inventory into connection entries."""
    inventory_path = Path(path)
    with inventory_path.open() as handle:
        data = yaml.safe_load(handle) or {}

    nodes = []
    for index, raw in enumerate(_inventory_nodes(data), start=1):
        address = raw.get("address") or raw.get("host") or raw.get("ip")
        if not address:
            raise ValueError(f"fleet node {index} is missing address")
        address = str(address)
        nodes.append(
            FleetNode(
                name=str(raw.get("name") or address),
                address=address,
                username=_env_value(raw, "username", "usernameEnv", "root"),
                password=_env_value(raw, "password", "passwordEnv", ""),
                port=_as_int(raw.get("port"), 443),
                insecure=_as_bool(raw.get("insecure"), True),
                use_http=_as_bool(raw.get("useHttp", raw.get("use_http")), False),
            )
        )
    return tuple(nodes)


def _temperature_summary(readings: tuple[Any, ...]) -> dict[str, int | float | None]:
    values = [
        value
        for value in (_as_float(reading.reading_celsius) for reading in readings)
        if value is not None
    ]
    max_celsius = max(values) if values else None
    return {
        "count": len(readings),
        "max_celsius": max_celsius,
    }


def _node_manager(node: FleetNode) -> CommandBase:
    return CommandBase(
        idrac_ip=node.address,
        idrac_username=node.username,
        idrac_password=node.password,
        idrac_port=node.port,
        insecure=node.insecure,
        is_http=node.use_http,
    )


def _error_row(node: FleetNode, exc: BaseException) -> dict[str, Any]:
    return {
        "name": node.name,
        "address": node.address,
        "ok": False,
        "powerState": None,
        "health": None,
        "state": None,
        "sensors": {"count": 0},
        "temperature": {"count": 0, "max_celsius": None},
        "error": str(exc),
    }


def read_node(node: FleetNode) -> dict[str, Any]:
    """Read one node through the typed facade and return a public summary."""
    try:
        manager = _node_manager(node)
        system = get_system(manager)
        sensors = get_sensors(manager)
        thermal = get_thermal(manager)
        return {
            "name": node.name,
            "address": node.address,
            "ok": True,
            "powerState": system.power_state,
            "health": system.health,
            "state": system.state,
            "sensors": {"count": len(sensors)},
            "temperature": _temperature_summary(thermal.temperatures),
            "error": None,
        }
    except (RedfishApiError, OSError, ValueError) as exc:
        return _error_row(node, exc)


def read_fleet(nodes: tuple[FleetNode, ...], concurrency: int) -> dict[str, Any]:
    """Fan out read-only status calls across inventory nodes."""
    if not nodes:
        return {"summary": {"total": 0, "ok": 0, "failed": 0}, "nodes": []}

    max_concurrency = max(1, min(int(concurrency or 1), len(nodes)))
    rows_by_index: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        future_to_index = {
            executor.submit(read_node, node): index
            for index, node in enumerate(nodes)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                rows_by_index[index] = future.result()
            except Exception as exc:
                rows_by_index[index] = _error_row(nodes[index], exc)

    rows = [rows_by_index[index] for index in range(len(nodes))]
    ok_count = sum(1 for row in rows if row["ok"])
    return {
        "summary": {
            "total": len(rows),
            "ok": ok_count,
            "failed": len(rows) - ok_count,
        },
        "nodes": rows,
    }


class FleetInventory(CommandBase,
                     scm_type=ApiRequestType.FleetInventory,
                     name="fleet",
                     metaclass=Singleton):
    """Read a YAML fleet inventory and summarize node health."""

    def __init__(self, *args, **kwargs):
        super(FleetInventory, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only fleet subcommand."""
        cmd_parser = argparse.ArgumentParser(add_help=False)
        cmd_parser.add_argument(
            "--inventory",
            required=True,
            help="YAML file with a top-level nodes list.",
        )
        cmd_parser.add_argument(
            "--concurrency",
            type=int,
            default=8,
            help="maximum concurrent BMC reads.",
        )
        return cmd_parser, "fleet", "read fleet inventory status from a YAML file"

    def execute(self,
                inventory: str,
                concurrency: int = 8,
                **kwargs) -> CommandResult:
        """Execute read-only fan-out over the configured inventory."""
        nodes = load_inventory(inventory)
        return CommandResult(read_fleet(nodes, concurrency), None, None, None)
