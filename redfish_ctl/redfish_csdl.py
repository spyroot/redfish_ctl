"""Helpers for reading Redfish CSDL action metadata from a local cache."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

_VERSION_SUFFIX = re.compile(r"\.v\d+_\d+_\d+$")


@dataclass(frozen=True)
class CsdlActionParameter:
    """One CSDL Action parameter from the published Redfish schema bundle."""

    name: str
    type_name: str
    allowable_values: tuple[str, ...] = ()
    nullable: bool = True


def _local_name(tag: str) -> str:
    """Strip the XML namespace from a qualified tag.

    :param tag: a possibly namespace-qualified XML tag.
    :return: the local element name without the ``{namespace}`` prefix.
    """
    return tag.rsplit("}", 1)[-1]


def _namespace_family(namespace: str) -> str:
    """Drop the versioned suffix from a CSDL namespace.

    :param namespace: a CSDL namespace, e.g. ``Manager.v1_5_0``.
    :return: the unversioned namespace family, e.g. ``Manager``.
    """
    return _VERSION_SUFFIX.sub("", namespace or "")


def _type_key(type_name: str) -> str:
    """Normalize a CSDL type reference to a ``family.Type`` lookup key.

    Unwraps a ``Collection(...)`` wrapper and strips the version from the
    namespace so the key matches parsed enum definitions.

    :param type_name: a CSDL type name, optionally wrapped in ``Collection(...)``.
    :return: the normalized ``family.Type`` key, or the cleaned input when it
        carries no namespace.
    """
    cleaned = (type_name or "").strip()
    if cleaned.startswith("Collection(") and cleaned.endswith(")"):
        cleaned = cleaned[len("Collection("):-1]
    parts = cleaned.split(".")
    if len(parts) >= 2:
        family = _namespace_family(".".join(parts[:-1]))
        return f"{family}.{parts[-1]}"
    return cleaned


def _default_schema_dir() -> Path:
    """Return the directory holding the local CSDL schema bundle.

    :return: the path from ``REDFISH_CSDL_DIR`` when set, otherwise the bundled
        ``tools/redfish-schemas`` directory.
    """
    configured = os.environ.get("REDFISH_CSDL_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "tools" / "redfish-schemas"


def _iter_schema_files(schema_dir: Path) -> tuple[Path, ...]:
    """List the CSDL schema files in a directory.

    :param schema_dir: directory to search recursively.
    :return: sorted ``*.xml`` paths, or an empty tuple when the directory does
        not exist.
    """
    if not schema_dir.exists():
        return ()
    return tuple(sorted(schema_dir.rglob("*.xml")))


def _parse_schema_file(
    path: Path,
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[tuple[str, str], list[CsdlActionParameter]],
]:
    """Parse one CSDL schema file for enum values and action parameters.

    Reads every ``Schema`` element, collecting ``EnumType`` members and
    ``Action`` parameters (dropping the bound first parameter when the action
    is bound). Entries are recorded under both the versioned namespace and its
    family.

    :param path: the CSDL ``*.xml`` file to parse.
    :return: a tuple ``(enums, raw_actions)`` where ``enums`` maps a
        ``namespace.Type`` to its member names and ``raw_actions`` maps a
        ``(namespace, action)`` pair to its parameter list.
    """
    root = ET.parse(path).getroot()
    enums: dict[str, tuple[str, ...]] = {}
    raw_actions: dict[tuple[str, str], list[CsdlActionParameter]] = {}

    for schema in root.iter():
        if _local_name(schema.tag) != "Schema":
            continue
        namespace = schema.attrib.get("Namespace", "")
        family = _namespace_family(namespace)
        for child in list(schema):
            name = child.attrib.get("Name", "")
            if not name:
                continue
            if _local_name(child.tag) == "EnumType":
                values = tuple(
                    member.attrib["Name"]
                    for member in list(child)
                    if _local_name(member.tag) == "Member" and member.attrib.get("Name")
                )
                enums[f"{namespace}.{name}"] = values
                enums[f"{family}.{name}"] = values
            elif _local_name(child.tag) == "Action":
                params = []
                parameters = [
                    param
                    for param in list(child)
                    if _local_name(param.tag) == "Parameter" and param.attrib.get("Name")
                ]
                if child.attrib.get("IsBound", "").lower() == "true" and parameters:
                    parameters = parameters[1:]
                for param in parameters:
                    params.append(
                        CsdlActionParameter(
                            name=param.attrib["Name"],
                            type_name=param.attrib.get("Type", ""),
                            nullable=param.attrib.get("Nullable", "true").lower() != "false",
                        )
                    )
                raw_actions[(namespace, name)] = params
                raw_actions[(family, name)] = params

    return enums, raw_actions


@lru_cache(maxsize=8)
def _load_action_parameters(
    schema_dir: str,
) -> dict[tuple[str, str], tuple[CsdlActionParameter, ...]]:
    """Load and cache all action parameters from a schema directory.

    Merges every schema file, then resolves each parameter's allowable values
    from the collected enum definitions. Results are cached per directory.

    :param schema_dir: directory holding the CSDL schema bundle.
    :return: a mapping of ``(namespace, action)`` to the action's resolved
        parameters.
    """
    enums: dict[str, tuple[str, ...]] = {}
    actions: dict[tuple[str, str], list[CsdlActionParameter]] = {}
    for path in _iter_schema_files(Path(schema_dir)):
        file_enums, file_actions = _parse_schema_file(path)
        enums.update(file_enums)
        actions.update(file_actions)

    resolved: dict[tuple[str, str], tuple[CsdlActionParameter, ...]] = {}
    for key, params in actions.items():
        resolved[key] = tuple(
            CsdlActionParameter(
                name=param.name,
                type_name=param.type_name,
                allowable_values=enums.get(_type_key(param.type_name), ()),
                nullable=param.nullable,
            )
            for param in params
        )
    return resolved


def action_parameters_for(
    full_action_type: str,
    schema_dir: str | os.PathLike | None = None,
) -> Mapping[str, CsdlActionParameter]:
    """Return CSDL parameters for a full Redfish action type, if cached locally.

    :param full_action_type: the full action type, e.g. ``#ComputerSystem.Reset``.
    :param schema_dir: directory holding the CSDL schema bundle; defaults to
        :func:`_default_schema_dir`.
    :return: a mapping of parameter name to its CSDL definition, empty when the
        action type is malformed or absent from the local bundle.
    """
    parts = (full_action_type or "").lstrip("#").split(".")
    if len(parts) < 2:
        return {}
    namespace, action_name = _namespace_family(".".join(parts[:-1])), parts[-1]
    root = Path(schema_dir) if schema_dir is not None else _default_schema_dir()
    params = _load_action_parameters(str(root)).get((namespace, action_name), ())
    return {param.name: param for param in params}
