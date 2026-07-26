#!/usr/bin/env python3
"""Convert one flat Redfish capture into a DSP2043-style resource tree.

The capture remains vendor evidence; this tool only changes its on-disk
addressing protocol.  A successful resource at ``/redfish/v1/X/Y`` is copied
byte-for-byte to ``<profile-root>/X/Y/index.json``.  Routes come from the
capture's route map or ``@odata.id`` links, never by splitting the lossy flat
fixture name on underscores.

The three commands are deliberately separate:

``plan``
    Validate and print the deterministic conversion report without writing.
``materialize``
    Require a passing plan, then atomically create the profile tree and report.
``verify``
    Compare an existing tree and report with the source archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator
from urllib.parse import unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "tests" / "corpus" / "manifest.json"
REPORT_SCHEMA = "redfish_ctl.corpus_tree_conversion_report/v1"
VERIFY_SCHEMA = "redfish_ctl.corpus_tree_verification_report/v1"
PROFILE_KIND = "vendor-corpus"
SERVICE_ROOT = "/redfish/v1"
JSON_PREFIX = "_redfish_v1"
METADATA_FIXTURE = "_redfish_v1_$metadata.xml"
STATUS_SIDECAR = "rest_api_map.status.json"
LEGACY_ROUTE_MAP = "rest_api_map.npy"
SAFE_ROUTE_MAP = "corpus_routes.json"
SAFE_ROUTE_MAP_SCHEMA = "redfish_ctl.corpus_routes/v1"
SIDECAR_NAMES = {
    "corpus_manifest.json",
    SAFE_ROUTE_MAP,
    STATUS_SIDECAR,
    LEGACY_ROUTE_MAP,
}
PROFILE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PERCENT_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")
ENCODED_SEPARATOR_RE = re.compile(r"%(?:2f|5c)", re.IGNORECASE)

# These bounds are intentionally well above the current largest committed
# corpus, while preventing an archive header from requesting unbounded memory.
MAX_ARCHIVE_MEMBERS = 20_000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024


class ConversionError(RuntimeError):
    """An input or filesystem condition prevents producing a report."""


class RouteError(ValueError):
    """A candidate cannot be represented by the directory protocol."""

    def __init__(self, reason: str, disposition: str = "unresolved") -> None:
        super().__init__(reason)
        self.reason = reason
        self.disposition = disposition


@dataclass(frozen=True)
class Candidate:
    """One authoritative route claim for a source fixture."""

    route: str
    source: str


@dataclass
class Conversion:
    """A deterministic conversion plan and the raw bytes it would emit."""

    report: dict[str, Any]
    payloads: dict[str, bytes]


@dataclass
class ArchiveData:
    """Validated regular files read from one corpus archive."""

    files: dict[str, bytes]
    source_sha256: str


def _json_load_bytes(payload: bytes, source: str) -> Any:
    """Decode strict UTF-8 JSON, rejecting non-standard numeric constants."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant {value}")

    try:
        return json.loads(
            payload.decode("utf-8"),
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON in {source}: {exc}") from exc


def _canonical_json(data: Any) -> bytes:
    """Return the contract's canonical JSON serialization with final newline."""

    return (
        json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_path(name: str) -> PurePosixPath:
    """Validate one tar member name before any member bytes are read."""

    if not name or "\\" in name or "\x00" in name:
        raise ConversionError(f"unsafe archive member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ConversionError(f"unsafe archive member path: {name!r}")
    return path


def _safe_source_root(arcname: str) -> PurePosixPath:
    root = _safe_archive_path(arcname.rstrip("/"))
    if len(root.parts) != 1:
        raise ConversionError("manifest arcname must identify one archive root")
    return root


def _read_archive(path: Path, arcname: str) -> ArchiveData:
    """Read validated files below ``arcname`` without extracting the archive."""

    if not path.is_file():
        raise ConversionError(f"corpus archive not found: {path}")
    with path.open("rb") as stream:
        if stream.read(120).startswith(b"version https://git-lfs.github.com/spec"):
            raise ConversionError(
                f"corpus archive is a bare Git-LFS pointer: {path}; hydrate it first"
            )

    root = _safe_source_root(arcname)
    files: dict[str, bytes] = {}
    total = 0
    try:
        archive = tarfile.open(path, mode="r:*")
    except (OSError, tarfile.TarError) as exc:
        raise ConversionError(f"cannot open corpus archive {path}: {exc}") from exc
    with archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ConversionError(
                f"archive has {len(members)} members; limit is {MAX_ARCHIVE_MEMBERS}"
            )
        for member in members:
            member_path = _safe_archive_path(member.name.rstrip("/"))
            try:
                relative = member_path.relative_to(root)
            except ValueError as exc:
                raise ConversionError(
                    f"archive member is outside manifest arcname {arcname!r}: "
                    f"{member.name!r}"
                ) from exc
            if member.isdir():
                continue
            if not member.isreg():
                raise ConversionError(
                    f"archive member type is forbidden: {member.name!r}"
                )
            if len(relative.parts) != 1:
                raise ConversionError(
                    f"regular file is not a direct child of {arcname!r}: "
                    f"{member.name!r}"
                )
            if member.size > MAX_MEMBER_BYTES:
                raise ConversionError(
                    f"archive member exceeds {MAX_MEMBER_BYTES} bytes: {member.name!r}"
                )
            total += member.size
            if total > MAX_TOTAL_BYTES:
                raise ConversionError(
                    f"archive expands beyond {MAX_TOTAL_BYTES} bytes"
                )
            name = relative.name
            if name in files:
                raise ConversionError(f"duplicate archive member: {member.name!r}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ConversionError(f"cannot read archive member: {member.name!r}")
            payload = extracted.read(MAX_MEMBER_BYTES + 1)
            if len(payload) != member.size:
                raise ConversionError(
                    f"archive member size mismatch: {member.name!r}"
                )
            files[name] = payload
    if not files:
        raise ConversionError(f"archive root {arcname!r} has no regular files")
    return ArchiveData(files=files, source_sha256=_sha256_file(path))


def canonicalize_route(candidate: str) -> str:
    """Return one canonical in-scope Redfish route or raise ``RouteError``."""

    if not isinstance(candidate, str) or not candidate:
        raise RouteError("route is not a non-empty string")
    parsed = urlsplit(candidate)
    if parsed.scheme:
        raise RouteError("scheme-not-allowed", disposition="excluded")
    if parsed.netloc:
        raise RouteError("authority-not-allowed", disposition="excluded")
    for match in re.finditer("%", parsed.path):
        if PERCENT_ESCAPE_RE.match(parsed.path, match.start()) is None:
            raise RouteError("malformed-percent-encoding")
    if ENCODED_SEPARATOR_RE.search(parsed.path):
        raise RouteError("encoded-path-separator")
    if parsed.query:
        raise RouteError("query-variant")
    if parsed.fragment:
        raise RouteError("fragment-selector", disposition="excluded")
    path = unquote(parsed.path)
    if "\\" in path:
        raise RouteError("backslash")
    if "\x00" in path:
        raise RouteError("nul")
    if path == SERVICE_ROOT + "/":
        path = SERVICE_ROOT
    elif path.endswith("/"):
        path = path[:-1]
    if not path.startswith("/"):
        raise RouteError("relative-uri")
    if path != SERVICE_ROOT and not path.startswith(SERVICE_ROOT + "/"):
        raise RouteError("outside-redfish-v1", disposition="excluded")
    suffix = path[len(SERVICE_ROOT):]
    if suffix:
        segments = suffix[1:].split("/")
        if any(segment == "" for segment in segments):
            raise RouteError("empty-internal-segment")
        if any(segment == "." for segment in segments):
            raise RouteError("dot-segment")
        if any(segment == ".." for segment in segments):
            raise RouteError("dot-dot-segment")
    return path


def _forward_filename(route: str, suffix: str = ".json") -> str:
    return "_" + route.strip("/").replace("/", "_") + suffix


def _destination_for_route(route: str, *, xml: bool = False) -> str:
    suffix = route[len(SERVICE_ROOT):].strip("/")
    terminal = "index.xml" if xml else "index.json"
    return f"{suffix}/{terminal}" if suffix else terminal


def _iter_odata_ids(value: Any) -> Iterator[str]:
    """Yield every string-valued ``@odata.id`` in deterministic walk order."""

    if isinstance(value, dict):
        for key in sorted(value):
            child = value[key]
            if key == "@odata.id" and isinstance(child, str):
                yield child
            yield from _iter_odata_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_odata_ids(child)


def _load_route_map(files: dict[str, bytes]) -> list[tuple[str, str]]:
    """Load the safe JSON route map; legacy/status sidecars are never authority."""

    payload = files.get(SAFE_ROUTE_MAP)
    if payload is None:
        return []
    try:
        value = _json_load_bytes(payload, SAFE_ROUTE_MAP)
    except ValueError as exc:
        raise ConversionError(str(exc)) from exc
    if not isinstance(value, dict) or value.get("schema") != SAFE_ROUTE_MAP_SCHEMA:
        raise ConversionError(
            f"{SAFE_ROUTE_MAP} must declare schema {SAFE_ROUTE_MAP_SCHEMA}"
        )
    rows = value.get("routes")
    if not isinstance(rows, list):
        raise ConversionError(f"{SAFE_ROUTE_MAP} routes must be an array")
    result: list[tuple[str, str]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ConversionError(f"{SAFE_ROUTE_MAP} routes[{index}] must be an object")
        route = row.get("route")
        fixture = row.get("sourceFixture")
        if not isinstance(route, str) or not route:
            raise ConversionError(
                f"{SAFE_ROUTE_MAP} routes[{index}].route must be a string"
            )
        if not isinstance(fixture, str) or not fixture:
            raise ConversionError(
                f"{SAFE_ROUTE_MAP} routes[{index}].sourceFixture must be a string"
            )
        basename = PurePosixPath(fixture)
        if basename.is_absolute() or len(basename.parts) != 1:
            raise ConversionError(
                f"{SAFE_ROUTE_MAP} routes[{index}].sourceFixture must be a basename"
            )
        result.append((route, fixture))
    return result


def _load_manifest_row(
    manifest_path: Path,
    vendor: str,
    model: str,
) -> tuple[dict[str, Any], Path]:
    """Resolve exactly one case-insensitive vendor/model manifest row."""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConversionError(f"cannot read corpus manifest {manifest_path}: {exc}") from exc
    rows = manifest.get("corpora") if isinstance(manifest, dict) else None
    if not isinstance(rows, list):
        raise ConversionError("corpus manifest must contain a corpora array")
    selected = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("vendor", "")).lower() == vendor.lower()
        and str(row.get("model", "")).lower() == model.lower()
    ]
    if len(selected) != 1:
        raise ConversionError(
            f"expected one corpus for {vendor}/{model}, found {len(selected)}"
        )
    row = selected[0]
    for field in ("vendor", "model", "tarball", "arcname"):
        if not isinstance(row.get(field), str) or not row[field]:
            raise ConversionError(f"corpus manifest row missing string field {field}")
    tarball_rel = PurePosixPath(row["tarball"])
    if tarball_rel.is_absolute() or any(
        part in {"", ".", ".."} for part in tarball_rel.parts
    ):
        raise ConversionError("manifest tarball must be a safe repository-relative path")
    resolved_manifest = manifest_path.resolve()
    try:
        resolved_manifest.relative_to(REPO_ROOT.resolve())
        manifest_base = REPO_ROOT.resolve()
    except ValueError:
        # External manifests are useful for bounded synthetic validation.  Their
        # archives remain contained beneath the manifest directory.
        manifest_base = resolved_manifest.parent
    archive = (manifest_base / Path(*tarball_rel.parts)).resolve()
    try:
        archive.relative_to(manifest_base)
    except ValueError as exc:
        raise ConversionError("manifest tarball resolves outside its allowed base") from exc
    return row, archive


def _route_issue(
    issues: dict[str, list[dict[str, str]]],
    fixture: str,
    source: str,
    raw: str,
    error: RouteError,
) -> None:
    issues[fixture].append(
        {
            "source": source,
            "candidate": raw,
            "reason": error.reason,
            "disposition": error.disposition,
        }
    )


def _add_candidate(
    candidates: dict[str, list[Candidate]],
    issues: dict[str, list[dict[str, str]]],
    fixture: str,
    source: str,
    raw: str,
) -> None:
    try:
        route = canonicalize_route(raw)
    except RouteError as exc:
        _route_issue(issues, fixture, source, raw, exc)
        return
    candidate = Candidate(route=route, source=source)
    if candidate not in candidates[fixture]:
        candidates[fixture].append(candidate)


def _agreed_route(candidates: Iterable[Candidate]) -> str | None:
    routes = {candidate.route for candidate in candidates}
    return next(iter(routes)) if len(routes) == 1 else None


def _canonical_alias_exclusion(
    fixture: str,
    candidates: dict[str, list[Candidate]],
    parsed: dict[str, Any],
    files: dict[str, bytes],
    fixture_lookup: dict[str, list[str]],
) -> dict[str, Any] | None:
    """Describe a proven non-canonical alias that may be safely excluded.

    An alias is eligible only when a discovered or explicit request route maps
    to this fixture, its top-level ``@odata.id`` names a different canonical
    route, the canonical fixture exists, and both payloads parse to equal JSON.
    Any missing evidence or semantic difference keeps the normal blocking
    disagreement behavior.
    """

    fixture_candidates = candidates.get(fixture, [])
    top_routes = {
        candidate.route
        for candidate in fixture_candidates
        if candidate.source == "top-level-odata-id"
    }
    if len(top_routes) != 1 or fixture not in parsed:
        return None
    canonical_route = next(iter(top_routes))
    alias_routes = sorted(
        {
            candidate.route
            for candidate in fixture_candidates
            if candidate.source in {"discovered-odata-link", "explicit-route-map"}
            and candidate.route != canonical_route
            and _forward_filename(candidate.route).casefold() == fixture.casefold()
        }
    )
    if not alias_routes:
        return None
    canonical_name = _forward_filename(canonical_route)
    canonical_matches = fixture_lookup.get(canonical_name.casefold(), [])
    if len(canonical_matches) != 1:
        return None
    canonical_fixture = canonical_matches[0]
    if canonical_fixture == fixture or canonical_fixture not in parsed:
        return None
    canonical_routes = {
        candidate.route for candidate in candidates.get(canonical_fixture, [])
    }
    if canonical_route not in canonical_routes:
        return None
    if parsed[fixture] != parsed[canonical_fixture]:
        return None
    return {
        "reason": "canonical-alias-subset",
        "route": alias_routes[0],
        "aliasRoutes": alias_routes,
        "canonicalRoute": canonical_route,
        "canonicalSourceFixture": canonical_fixture,
        "payloadComparison": (
            "byte-identical"
            if files[fixture] == files[canonical_fixture]
            else "json-equivalent"
        ),
    }


def _tree_sha256(payloads: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for destination in sorted(payloads):
        digest.update(destination.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_bytes(payloads[destination]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _collision_sets(route_by_fixture: dict[str, str]) -> list[dict[str, Any]]:
    """Return route, case-folded destination, and file/directory collisions."""

    conflicts: list[dict[str, Any]] = []
    fixtures_by_route: dict[str, list[str]] = defaultdict(list)
    fixtures_by_destination: dict[str, list[tuple[str, str]]] = defaultdict(list)
    destinations: dict[str, tuple[str, str]] = {}
    for fixture, route in route_by_fixture.items():
        destination = _destination_for_route(
            route,
            xml=fixture == METADATA_FIXTURE,
        )
        fixtures_by_route[route].append(fixture)
        fixtures_by_destination[destination.casefold()].append((fixture, destination))
        destinations[fixture] = (route, destination)
    for route, fixtures in sorted(fixtures_by_route.items()):
        if len(fixtures) > 1:
            conflicts.append(
                {
                    "kind": "multiple-source-fixtures-to-route",
                    "route": route,
                    "sourceFixtures": sorted(fixtures),
                }
            )
    for folded, rows in sorted(fixtures_by_destination.items()):
        if len(rows) > 1:
            conflicts.append(
                {
                    "kind": "case-folded-destination-collision",
                    "destination": folded,
                    "sourceFixtures": sorted(row[0] for row in rows),
                }
            )
    for fixture, (_, destination) in sorted(destinations.items()):
        file_parts = PurePosixPath(destination).parts
        for other, (_, other_destination) in sorted(destinations.items()):
            if fixture == other:
                continue
            other_parts = PurePosixPath(other_destination).parts
            if len(other_parts) > len(file_parts) and other_parts[: len(file_parts)] == file_parts:
                conflicts.append(
                    {
                        "kind": "file-directory-collision",
                        "destination": destination,
                        "sourceFixtures": sorted([fixture, other]),
                    }
                )
    unique = {json.dumps(item, sort_keys=True): item for item in conflicts}
    return [unique[key] for key in sorted(unique)]


def build_conversion(
    manifest_path: Path,
    vendor: str,
    model: str,
) -> Conversion:
    """Build a deterministic, non-mutating conversion plan."""

    manifest_path = Path(manifest_path)
    row, archive_path = _load_manifest_row(manifest_path, vendor, model)
    profile_id = f"{row['vendor'].lower()}-{row['model'].lower()}"
    if not PROFILE_ID_RE.fullmatch(profile_id):
        raise ConversionError(f"invalid profile id derived from manifest: {profile_id}")
    archive = _read_archive(archive_path, row["arcname"])
    files = archive.files
    route_map = _load_route_map(files)

    fixture_names = sorted(
        name
        for name in files
        if (
            (name.startswith(JSON_PREFIX) and name.endswith(".json"))
            or name == METADATA_FIXTURE
        )
        and name not in SIDECAR_NAMES
    )
    sidecars = sorted(name for name in files if name not in fixture_names)
    fixture_lookup: dict[str, list[str]] = defaultdict(list)
    for fixture in fixture_names:
        fixture_lookup[fixture.casefold()].append(fixture)

    parsed: dict[str, Any] = {}
    candidates: dict[str, list[Candidate]] = defaultdict(list)
    issues: dict[str, list[dict[str, str]]] = defaultdict(list)
    status: dict[str, str] = {}
    reasons: dict[str, list[dict[str, Any]]] = defaultdict(list)
    conflicts: list[dict[str, Any]] = []

    for folded, same_name in sorted(fixture_lookup.items()):
        if len(same_name) > 1:
            conflicts.append(
                {
                    "kind": "case-folded-source-fixture-collision",
                    "sourceFixtures": sorted(same_name),
                    "foldedName": folded,
                }
            )

    for fixture in fixture_names:
        if fixture.endswith(".error.json"):
            status[fixture] = "excluded"
            reasons[fixture].append({"reason": "captured-non-success-response"})
            continue
        if "#" in fixture:
            # Discovery historically persisted JSON Pointer fragment reads as
            # separate flat files.  A fragment is client-side selection, not
            # an HTTP resource route, so it has no DSP2043 tree destination.
            status[fixture] = "excluded"
            reasons[fixture].append({"reason": "fragment-selector-capture"})
            continue
        payload = files[fixture]
        if fixture == METADATA_FIXTURE:
            try:
                ET.fromstring(payload)
            except ET.ParseError as exc:
                status[fixture] = "unresolved"
                reasons[fixture].append(
                    {"reason": "invalid-xml", "detail": str(exc)}
                )
                continue
            _add_candidate(
                candidates,
                issues,
                fixture,
                "metadata-anchor",
                f"{SERVICE_ROOT}/$metadata",
            )
            continue
        try:
            value = _json_load_bytes(payload, fixture)
        except ValueError as exc:
            status[fixture] = "unresolved"
            reasons[fixture].append({"reason": "invalid-json", "detail": str(exc)})
            continue
        if not isinstance(value, (dict, list)):
            status[fixture] = "unresolved"
            reasons[fixture].append({"reason": "json-not-object-or-array"})
            continue
        parsed[fixture] = value
        if isinstance(value, dict) and isinstance(value.get("@odata.id"), str):
            _add_candidate(
                candidates,
                issues,
                fixture,
                "top-level-odata-id",
                value["@odata.id"],
            )

    for route, fixture in sorted(route_map):
        matches = fixture_lookup.get(fixture.casefold(), [])
        if len(matches) != 1:
            conflicts.append(
                {
                    "kind": "route-map-source-missing-or-ambiguous",
                    "route": route,
                    "sourceFixture": fixture,
                }
            )
            continue
        _add_candidate(
            candidates,
            issues,
            matches[0],
            "explicit-route-map",
            route,
        )

    root_matches = fixture_lookup.get("_redfish_v1.json", [])
    if len(root_matches) == 1:
        _add_candidate(
            candidates,
            issues,
            root_matches[0],
            "service-root-anchor",
            SERVICE_ROOT,
        )

    # Recover otherwise-unroutable fixtures only by following @odata.id links
    # from the ServiceRoot.  The forward-flattened filename is a lookup/check,
    # never a reverse parser.
    queue: deque[str] = deque(root_matches)
    visited: set[str] = set()
    while queue:
        fixture = queue.popleft()
        if fixture in visited or fixture not in parsed:
            continue
        visited.add(fixture)
        route = _agreed_route(candidates.get(fixture, []))
        if route is None:
            continue
        linked_routes: set[str] = set()
        for raw_link in set(_iter_odata_ids(parsed[fixture])):
            try:
                linked_routes.add(canonicalize_route(raw_link))
            except RouteError:
                continue
        for linked_route in sorted(linked_routes):
            linked_name = _forward_filename(linked_route).casefold()
            matches = fixture_lookup.get(linked_name, [])
            if len(matches) != 1:
                continue
            target = matches[0]
            _add_candidate(
                candidates,
                issues,
                target,
                "discovered-odata-link",
                linked_route,
            )
            if target not in visited:
                queue.append(target)

    # A vendor may expose one canonical resource through an additional request
    # path while returning the canonical @odata.id in both payloads.  Exclude
    # that alias only when the canonical capture exists and the parsed payloads
    # are equivalent; this preserves all unique data without inventing a route.
    for fixture in fixture_names:
        if status.get(fixture) in {"excluded", "unresolved"}:
            continue
        alias = _canonical_alias_exclusion(
            fixture,
            candidates,
            parsed,
            files,
            fixture_lookup,
        )
        if alias is not None:
            status[fixture] = "excluded"
            reasons[fixture].append(alias)

    # Detect duplicate canonical claims before the forward-filename check.
    # Otherwise a badly named duplicate could be rejected alone while the
    # other claimant was emitted, violating the all-claimants blocking rule.
    fixtures_by_claimed_route: dict[str, list[str]] = defaultdict(list)
    for fixture in fixture_names:
        if status.get(fixture) in {"excluded", "unresolved"}:
            continue
        if issues.get(fixture):
            continue
        routes = {candidate.route for candidate in candidates.get(fixture, [])}
        if len(routes) == 1:
            fixtures_by_claimed_route[next(iter(routes))].append(fixture)
    for route, fixtures in sorted(fixtures_by_claimed_route.items()):
        if len(fixtures) < 2:
            continue
        conflict = {
            "kind": "multiple-source-fixtures-to-route",
            "route": route,
            "sourceFixtures": sorted(fixtures),
        }
        conflicts.append(conflict)
        for fixture in sorted(fixtures):
            status[fixture] = "unresolved"
            reasons[fixture].append(
                {
                    "reason": "multiple-source-fixtures-to-route",
                    "route": route,
                    "sourceFixtures": sorted(fixtures),
                }
            )

    route_by_fixture: dict[str, str] = {}
    for fixture in fixture_names:
        if status.get(fixture) in {"excluded", "unresolved"}:
            continue
        fixture_issues = issues.get(fixture, [])
        valid = candidates.get(fixture, [])
        routes = sorted({candidate.route for candidate in valid})
        if fixture_issues:
            excluded_only = (
                not routes
                and all(issue["disposition"] == "excluded" for issue in fixture_issues)
            )
            status[fixture] = "excluded" if excluded_only else "unresolved"
            reasons[fixture].extend(fixture_issues)
            if not excluded_only:
                conflicts.append(
                    {
                        "kind": "invalid-route-candidate",
                        "sourceFixture": fixture,
                        "candidates": fixture_issues,
                    }
                )
            continue
        if not routes:
            status[fixture] = "unresolved"
            reasons[fixture].append({"reason": "no-authoritative-route"})
            continue
        if len(routes) != 1:
            status[fixture] = "unresolved"
            reasons[fixture].append(
                {
                    "reason": "authoritative-route-disagreement",
                    "routes": routes,
                }
            )
            conflicts.append(
                {
                    "kind": "source-fixture-to-multiple-routes",
                    "sourceFixture": fixture,
                    "routes": routes,
                }
            )
            continue
        route = routes[0]
        expected = _forward_filename(
            route,
            suffix=".xml" if fixture == METADATA_FIXTURE else ".json",
        )
        if expected.casefold() != fixture.casefold():
            status[fixture] = "unresolved"
            reasons[fixture].append(
                {
                    "reason": "forward-filename-mismatch",
                    "route": route,
                    "expected": expected,
                }
            )
            conflicts.append(
                {
                    "kind": "forward-filename-mismatch",
                    "sourceFixture": fixture,
                    "route": route,
                    "expectedSourceFixture": expected,
                }
            )
            continue
        route_by_fixture[fixture] = route

    collision_conflicts = _collision_sets(route_by_fixture)
    conflicts.extend(collision_conflicts)
    collision_fixtures = {
        fixture
        for conflict in collision_conflicts
        for fixture in conflict.get("sourceFixtures", [])
    }
    for fixture in sorted(collision_fixtures):
        route_by_fixture.pop(fixture, None)
        status[fixture] = "unresolved"
        reasons[fixture].append({"reason": "destination-collision"})

    payloads: dict[str, bytes] = {}
    mappings: list[dict[str, str]] = []
    emitted_route_to_fixture: dict[str, str] = {}
    for fixture, route in sorted(route_by_fixture.items(), key=lambda item: (item[1], item[0])):
        destination = _destination_for_route(
            route,
            xml=fixture == METADATA_FIXTURE,
        )
        payload = files[fixture]
        payloads[destination] = payload
        status[fixture] = "emitted"
        emitted_route_to_fixture[route] = fixture
        mappings.append(
            {
                "route": route,
                "sourceFixture": fixture,
                "destination": destination,
                "sha256": _sha256_bytes(payload),
            }
        )

    missing_links: list[dict[str, Any]] = []
    blocking_link = False
    emitted_routes = set(emitted_route_to_fixture)
    for source_route, fixture in sorted(emitted_route_to_fixture.items()):
        value = parsed.get(fixture)
        if value is None:
            continue
        for raw_link in sorted(set(_iter_odata_ids(value))):
            try:
                route = canonicalize_route(raw_link)
            except RouteError as exc:
                entry = {
                    "sourceRoute": source_route,
                    "candidate": raw_link,
                    "disposition": exc.disposition,
                    "reason": exc.reason,
                }
                missing_links.append(entry)
                if exc.disposition != "excluded":
                    blocking_link = True
                continue
            if route in emitted_routes:
                continue
            expected = _forward_filename(route).casefold()
            matches = fixture_lookup.get(expected, [])
            missing_links.append(
                {
                    "sourceRoute": source_route,
                    "route": route,
                    "disposition": "missing",
                    "reason": (
                        "source-fixture-not-emitted"
                        if matches
                        else "source-capture-missing"
                    ),
                }
            )

    for fixture in fixture_names:
        if fixture not in status:
            status[fixture] = "unresolved"
            reasons[fixture].append({"reason": "internal-unclassified-source"})

    excluded = [
        {"sourceFixture": fixture, **reason}
        for fixture in sorted(fixture_names)
        if status[fixture] == "excluded"
        for reason in reasons[fixture]
    ]
    unresolved = [
        {"sourceFixture": fixture, **reason}
        for fixture in sorted(fixture_names)
        if status[fixture] == "unresolved"
        for reason in reasons[fixture]
    ]
    conflict_files = {
        fixture
        for conflict in conflicts
        for fixture in (
            conflict.get("sourceFixtures", [])
            + ([conflict["sourceFixture"]] if "sourceFixture" in conflict else [])
        )
        if fixture in fixture_names
    }
    counts = {
        "inputRegularFiles": len(files),
        "emittedFiles": sum(value == "emitted" for value in status.values()),
        "excludedFiles": sum(value == "excluded" for value in status.values()),
        "unresolvedFiles": sum(value == "unresolved" for value in status.values()),
        "sidecarFiles": len(sidecars),
        "conflictFiles": len(conflict_files),
    }
    accountability = (
        counts["inputRegularFiles"]
        == counts["emittedFiles"]
        + counts["excludedFiles"]
        + counts["unresolvedFiles"]
        + counts["sidecarFiles"]
    )
    service_root_present = SERVICE_ROOT in emitted_routes
    conflicts = sorted(
        {json.dumps(item, sort_keys=True): item for item in conflicts}.values(),
        key=lambda item: json.dumps(item, sort_keys=True),
    )
    missing_links = sorted(
        {json.dumps(item, sort_keys=True): item for item in missing_links}.values(),
        key=lambda item: (
            item.get("sourceRoute", ""),
            item.get("route", item.get("candidate", "")),
            item.get("reason", ""),
        ),
    )
    passed = (
        not conflicts
        and counts["unresolvedFiles"] == 0
        and service_root_present
        and accountability
        and not blocking_link
    )
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "profileId": profile_id,
        "profileKind": PROFILE_KIND,
        "source": {
            "vendor": row["vendor"],
            "model": row["model"],
            "tarball": row["tarball"],
            "arcname": row["arcname"],
        },
        "sourceSha256": archive.source_sha256,
        "treeSha256": _tree_sha256(payloads),
        "counts": counts,
        "mappings": mappings,
        "excluded": excluded,
        "unresolved": unresolved,
        "missingLinks": missing_links,
        "conflicts": conflicts,
        "checks": {
            "serviceRootPresent": service_root_present,
            "accountabilityEquation": accountability,
            "linkClosureAccounted": not blocking_link,
        },
        "result": "pass" if passed else "fail",
    }
    return Conversion(report=report, payloads=payloads)


def _profile_paths(conversion: Conversion, output: Path) -> tuple[Path, Path]:
    output = Path(output)
    return (
        output / conversion.report["profileId"],
        output / f"{conversion.report['profileId']}.conversion.json",
    )


def _existing_tree_findings(profile_root: Path, payloads: dict[str, bytes]) -> list[str]:
    findings: list[str] = []
    if profile_root.is_symlink():
        return ["profile root is a symlink"]
    if not profile_root.exists():
        return ["profile root is missing"]
    if not profile_root.is_dir():
        return ["profile root is not a directory"]
    actual: dict[str, Path] = {}
    for path in sorted(profile_root.rglob("*")):
        relative = path.relative_to(profile_root).as_posix()
        if path.is_symlink():
            findings.append(f"symlink is forbidden: {relative}")
        elif path.is_file():
            actual[relative] = path
    expected_names = set(payloads)
    actual_names = set(actual)
    for name in sorted(expected_names - actual_names):
        findings.append(f"missing file: {name}")
    for name in sorted(actual_names - expected_names):
        findings.append(f"unexpected file: {name}")
    for name in sorted(expected_names & actual_names):
        if actual[name].read_bytes() != payloads[name]:
            findings.append(f"content mismatch: {name}")
    return findings


def materialize(conversion: Conversion, output: Path) -> dict[str, Any]:
    """Atomically create a passing conversion, refusing mismatched output."""

    if conversion.report["result"] != "pass":
        raise ConversionError("materialize requires a conversion plan with result=pass")
    output = Path(output)
    profile_root, report_path = _profile_paths(conversion, output)
    report_bytes = _canonical_json(conversion.report)
    if output.is_symlink() or profile_root.is_symlink() or report_path.is_symlink():
        raise ConversionError("output paths must not be symlinks")
    if profile_root.exists():
        findings = _existing_tree_findings(profile_root, conversion.payloads)
        if findings:
            raise ConversionError("existing output mismatch: " + "; ".join(findings[:5]))
    if report_path.exists() and report_path.read_bytes() != report_bytes:
        raise ConversionError(f"existing output mismatch: {report_path.name}")

    output.mkdir(parents=True, exist_ok=True)
    if not profile_root.exists():
        temporary = Path(tempfile.mkdtemp(prefix=f".{profile_root.name}.", dir=output))
        try:
            for relative, payload in sorted(conversion.payloads.items()):
                destination = temporary / Path(*PurePosixPath(relative).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            os.replace(temporary, profile_root)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    if not report_path.exists():
        temporary_report = report_path.with_name(f".{report_path.name}.{os.getpid()}.tmp")
        try:
            temporary_report.write_bytes(report_bytes)
            os.replace(temporary_report, report_path)
        finally:
            if temporary_report.exists():
                temporary_report.unlink()
    return conversion.report


def _observed_tree_sha256(profile_root: Path) -> str | None:
    if not profile_root.is_dir() or profile_root.is_symlink():
        return None
    payloads: dict[str, bytes] = {}
    for path in sorted(profile_root.rglob("*")):
        if path.is_symlink():
            return None
        if path.is_file():
            payloads[path.relative_to(profile_root).as_posix()] = path.read_bytes()
    return _tree_sha256(payloads)


def verify(conversion: Conversion, output: Path) -> dict[str, Any]:
    """Return a read-only verification report for one materialized tree."""

    output = Path(output)
    profile_root, report_path = _profile_paths(conversion, output)
    findings = _existing_tree_findings(profile_root, conversion.payloads)
    expected_report = _canonical_json(conversion.report)
    if report_path.is_symlink():
        findings.append("conversion report is a symlink")
    elif not report_path.is_file():
        findings.append("conversion report is missing")
    elif report_path.read_bytes() != expected_report:
        findings.append("conversion report mismatch")
    if conversion.report["result"] != "pass":
        findings.append("source conversion plan does not pass")
    return {
        "schema": VERIFY_SCHEMA,
        "profileId": conversion.report["profileId"],
        "sourceSha256": conversion.report["sourceSha256"],
        "expectedTreeSha256": conversion.report["treeSha256"],
        "observedTreeSha256": _observed_tree_sha256(profile_root),
        "findings": sorted(set(findings)),
        "result": "pass" if not findings else "fail",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "materialize", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
        subparser.add_argument("--vendor", required=True)
        subparser.add_argument("--model", required=True)
        if command != "plan":
            subparser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        conversion = build_conversion(args.manifest, args.vendor, args.model)
        if args.command == "plan":
            report = conversion.report
        elif args.command == "materialize":
            report = materialize(conversion, args.output)
        else:
            report = verify(conversion, args.output)
    except (ConversionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(_canonical_json(report))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
