"""Validate Redfish JSON against the DMTF DSP8010 schemas.

Maps a resource's ``@odata.type`` to its unversioned (version-resilient) JSON
Schema and validates it with ``jsonschema`` + ``referencing``. Schema files are
cached under ``tools/redfish-schemas/``; a missing one is fetched once from
``redfish.dmtf.org`` and cached for offline reuse. Set ``REDFISH_SCHEMA_OFFLINE=1``
to forbid network and require a pre-vendored directory.

Dell ``Oem`` blocks are not part of the standard schema, so by default the ``Oem``
subtree is stripped before validation — we check the standard surface, not Dell's
private extensions. Resources whose ``@odata.type`` is itself an OEM type (e.g.
``#DellBootSources...``) have no standard schema and raise ``SchemaUnavailable``.

Author Mus spyroot@gmail.com
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent / "redfish-schemas"
PREFIX = "http://redfish.dmtf.org/schemas/v1/"
_REGISTRY = None


class SchemaUnavailable(RuntimeError):
    """The schema for a resource could not be found (OEM type, or offline+missing)."""


def _schema_file(uri: str) -> Path:
    name = uri[len(PREFIX):] if uri.startswith(PREFIX) else Path(uri).name
    return SCHEMA_DIR / name.split("#", 1)[0]


def _load_schema_doc(uri: str) -> dict:
    path = _schema_file(uri)
    if path.exists():
        return json.loads(path.read_text())
    if os.environ.get("REDFISH_SCHEMA_OFFLINE"):
        raise SchemaUnavailable(f"missing vendored schema for {uri} (offline mode)")
    url = uri.split("#", 1)[0]
    try:
        data = urllib.request.urlopen(url, timeout=30).read()
    except urllib.error.HTTPError as err:
        raise SchemaUnavailable(f"no standard schema at {url} ({err.code})") from err
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return json.loads(data)


def _schema_runtime():
    from jsonschema import Draft7Validator
    from referencing import Registry, Resource
    from referencing.exceptions import Unresolvable
    from referencing.jsonschema import DRAFT7

    return Draft7Validator, Registry, Resource, Unresolvable, DRAFT7


def _retrieve(uri: str):
    _, _, resource_cls, _, draft7 = _schema_runtime()
    return resource_cls.from_contents(
        _load_schema_doc(uri),
        default_specification=draft7,
    )


def _registry():
    global _REGISTRY
    if _REGISTRY is None:
        _, registry_cls, _, _, _ = _schema_runtime()
        _REGISTRY = registry_cls(retrieve=_retrieve)
    return _REGISTRY


def schema_ref_for(odata_type: str) -> str:
    """Return the unversioned schema ``$ref`` for a Redfish ``@odata.type``.

    Handles versioned (``#ComputerSystem.v1_16_0.ComputerSystem``), collection
    (``#ChassisCollection.ChassisCollection``), and old-format
    (``#TaskService.0.94.0.Task``) types: namespace is the first segment, the
    type is the last, any version in between is ignored.
    """
    parts = odata_type.lstrip("#").split(".")
    if len(parts) < 2 or not parts[0] or not parts[-1]:
        raise ValueError(f"unrecognized @odata.type: {odata_type!r}")
    namespace, type_name = parts[0], parts[-1]
    return f"{PREFIX}{namespace}.json#/definitions/{type_name}"


def _strip_oem(obj) -> None:
    if isinstance(obj, dict):
        obj.pop("Oem", None)
        for value in obj.values():
            _strip_oem(value)
    elif isinstance(obj, list):
        for value in obj:
            _strip_oem(value)


def _reduce_collection_members(obj: dict) -> None:
    """Reduce a collection's ``Members`` to references for validation.

    Redfish collection schemas type ``Members`` as references (``{@odata.id}``).
    Real services (notably Dell iDRAC, and any ``$expand`` response) inline the
    full member objects. We validate the collection's reference structure here;
    each inlined member is validated on its own as its individual resource.
    """
    members = obj.get("Members")
    if isinstance(members, list):
        obj["Members"] = [
            {"@odata.id": m["@odata.id"]}
            if isinstance(m, dict) and "@odata.id" in m else m
            for m in members
        ]


def validate_payload(payload: dict, strip_oem: bool = True) -> list:
    """Validate a Redfish resource. Returns a sorted list of errors ([] = valid).

    Raises ``SchemaUnavailable`` when the resource type has no standard schema
    (e.g. a Dell OEM type), and ``ValueError`` when ``@odata.type`` is missing.
    """
    odata_type = payload.get("@odata.type")
    if not odata_type:
        raise ValueError("payload has no @odata.type")
    body = deepcopy(payload)
    if strip_oem:
        _strip_oem(body)
    _reduce_collection_members(body)
    draft7_validator, _, _, unresolvable, _ = _schema_runtime()
    validator = draft7_validator(
        {"$ref": schema_ref_for(odata_type)},
        registry=_registry(),
    )
    try:
        errors = list(validator.iter_errors(body))
    except unresolvable as err:
        # a referenced schema (often an OEM type) has no standard definition
        raise SchemaUnavailable(str(err)) from err
    return sorted(errors, key=lambda e: list(e.absolute_path))


def _entry_path(path: Path, root: Path) -> str:
    if root.is_file():
        return path.name
    return str(path.relative_to(root))


def _format_validation_error(error) -> str:
    location = ".".join(str(part) for part in error.absolute_path)
    if not location:
        location = "<root>"
    return f"{location}: {error.message}"


def validate_tree(root: Path, strip_oem: bool = True) -> dict:
    """Validate every JSON file under ``root`` and classify each result."""
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(root)
    if root.is_file():
        files = [root] if root.suffix == ".json" else []
    else:
        files = sorted(root.rglob("*.json"))
    summary = {
        "root": str(root),
        "counts": {"files": len(files), "valid": 0, "error": 0, "skipped": 0},
        "valid": [],
        "error": [],
        "skipped": [],
    }
    for path in files:
        entry = _entry_path(path, root)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            summary["error"].append(
                {"path": entry, "errors": [f"invalid JSON: {exc.msg}"]}
            )
            continue
        if not isinstance(payload, dict):
            summary["skipped"].append(
                {"path": entry, "reason": "payload is not a JSON object"}
            )
            continue
        try:
            errors = validate_payload(payload, strip_oem=strip_oem)
        except (SchemaUnavailable, ValueError) as exc:
            summary["skipped"].append({"path": entry, "reason": str(exc)})
            continue
        except Exception as exc:
            summary["error"].append(
                {"path": entry, "errors": [f"validation failed: {exc}"]}
            )
            continue
        if errors:
            summary["error"].append(
                {
                    "path": entry,
                    "errors": [_format_validation_error(err) for err in errors],
                }
            )
        else:
            summary["valid"].append(entry)
    summary["counts"]["valid"] = len(summary["valid"])
    summary["counts"]["error"] = len(summary["error"])
    summary["counts"]["skipped"] = len(summary["skipped"])
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Redfish JSON files against cached DMTF schemas.",
    )
    parser.add_argument("root", type=Path, help="JSON file or directory tree to validate")
    parser.add_argument(
        "--no-strip-oem",
        action="store_false",
        dest="strip_oem",
        help="validate OEM blocks instead of stripping them first",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def _print_summary(summary: dict) -> None:
    counts = summary["counts"]
    print(f"root: {summary['root']}")
    print(
        "files: {files} valid: {valid} error: {error} skipped: {skipped}".format(
            **counts,
        )
    )
    if summary["error"]:
        print("errors:")
        for entry in summary["error"]:
            print(f"  {entry['path']}")
            for error in entry["errors"]:
                print(f"    - {error}")
    if summary["skipped"]:
        print("skipped:")
        for entry in summary["skipped"]:
            print(f"  {entry['path']}: {entry['reason']}")


def main(argv: list[str] | None = None) -> int:
    """Run the directory validator CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        summary = validate_tree(args.root, strip_oem=args.strip_oem)
    except FileNotFoundError as exc:
        print(f"redfish_validate: not found: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        _print_summary(summary)
    return 1 if summary["counts"]["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
