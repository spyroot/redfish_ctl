"""Manifest-backed index of committed Redfish fixture sets."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "tests" / "fixtures_catalog.json"
_VALID_STATUSES = {"active", "pending"}


class CatalogError(ValueError):
    """Raised when the fixture catalog manifest is malformed."""


@dataclass(frozen=True)
class FixtureSet:
    key: str
    vendor: str
    generation: str
    path: str
    status: str
    redfish_version: str | None = None
    oem_types: tuple[str, ...] = ()
    source: str | None = None
    reason: str | None = None
    notes: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FixtureSet":
        """Build a :class:`FixtureSet` from a manifest mapping.

        :param data: one fixture-set entry from the catalog manifest.
        :return: the validated fixture set.
        :raises CatalogError: if required fields are missing, the status is
            invalid, a pending set lacks a reason, or ``oem_types`` is not a list.
        """
        required = ("key", "vendor", "generation", "path", "status")
        missing = [field for field in required if not data.get(field)]
        if missing:
            raise CatalogError(f"fixture set missing required fields: {', '.join(missing)}")

        status = str(data["status"])
        if status not in _VALID_STATUSES:
            raise CatalogError(f"invalid fixture set status {status!r}")
        if status == "pending" and not data.get("reason"):
            raise CatalogError("pending fixture sets must include a reason")

        oem_types = data.get("oem_types") or []
        if not isinstance(oem_types, list):
            raise CatalogError("fixture set oem_types must be a list")

        return cls(
            key=str(data["key"]),
            vendor=str(data["vendor"]),
            generation=str(data["generation"]),
            path=str(data["path"]),
            status=status,
            redfish_version=data.get("redfish_version"),
            oem_types=tuple(str(value) for value in oem_types),
            source=data.get("source"),
            reason=data.get("reason"),
            notes=data.get("notes"),
        )

    def resolve_path(self, repo_root: Path | None = None) -> Path:
        """Resolve this set's path to an absolute filesystem path.

        :param repo_root: repository root to resolve against; defaults to the
            package's repository root.
        :return: the absolute path to the fixture-set directory.
        """
        base = repo_root or REPO_ROOT
        return (base / self.path).resolve()

    def json_file_count(self, repo_root: Path | None = None) -> int:
        """Count the JSON files under this set's resolved path.

        :param repo_root: repository root to resolve against; defaults to the
            package's repository root.
        :return: number of ``*.json`` files found, or 0 when the path is absent.
        """
        root = self.resolve_path(repo_root)
        if not root.exists():
            return 0
        return sum(1 for _path in root.rglob("*.json"))

    @property
    def is_active(self) -> bool:
        """Whether this fixture set is marked active.

        :return: True when the status is ``active``.
        """
        return self.status == "active"

    @property
    def is_pending(self) -> bool:
        """Whether this fixture set is marked pending.

        :return: True when the status is ``pending``.
        """
        return self.status == "pending"


@dataclass(frozen=True)
class FixtureCatalog:
    manifest_path: Path
    schema_version: int
    sets: tuple[FixtureSet, ...]

    @classmethod
    def from_manifest(cls, manifest_path: Path = DEFAULT_MANIFEST) -> "FixtureCatalog":
        """Load and validate a fixture catalog from a manifest file.

        :param manifest_path: path to the catalog manifest JSON.
        :return: the parsed catalog.
        :raises CatalogError: if the manifest cannot be read, is not valid JSON,
            has an unexpected schema version, lacks a ``sets`` list, or contains
            a duplicate fixture-set key.
        """
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CatalogError(f"cannot read fixture catalog manifest: {manifest_path}") from exc
        except json.JSONDecodeError as exc:
            raise CatalogError(f"fixture catalog manifest is not valid JSON: {manifest_path}") from exc

        if raw.get("schema_version") != 1:
            raise CatalogError("fixture catalog schema_version must be 1")
        raw_sets = raw.get("sets")
        if not isinstance(raw_sets, list):
            raise CatalogError("fixture catalog sets must be a list")

        fixture_sets = tuple(FixtureSet.from_mapping(item) for item in raw_sets)
        seen: set[str] = set()
        for fixture_set in fixture_sets:
            if fixture_set.key in seen:
                raise CatalogError(f"duplicate fixture set key: {fixture_set.key}")
            seen.add(fixture_set.key)

        return cls(
            manifest_path=manifest_path,
            schema_version=1,
            sets=fixture_sets,
        )

    def require(self, key: str) -> FixtureSet:
        """Return the fixture set with the given key.

        :param key: fixture-set key to look up.
        :return: the matching fixture set.
        :raises CatalogError: if no set has that key.
        """
        for fixture_set in self.sets:
            if fixture_set.key == key:
                return fixture_set
        raise CatalogError(f"unknown fixture set: {key}")

    def active_sets(self) -> tuple[FixtureSet, ...]:
        """Return all active fixture sets.

        :return: the fixture sets whose status is ``active``.
        """
        return tuple(fixture_set for fixture_set in self.sets if fixture_set.is_active)

    def pending_sets(self) -> tuple[FixtureSet, ...]:
        """Return all pending fixture sets.

        :return: the fixture sets whose status is ``pending``.
        """
        return tuple(fixture_set for fixture_set in self.sets if fixture_set.is_pending)

    def by_vendor(self, vendor: str) -> tuple[FixtureSet, ...]:
        """Return the fixture sets for one vendor.

        :param vendor: vendor name to match.
        :return: the fixture sets whose vendor equals ``vendor``.
        """
        return tuple(fixture_set for fixture_set in self.sets if fixture_set.vendor == vendor)


def load_catalog(manifest_path: Path = DEFAULT_MANIFEST) -> FixtureCatalog:
    """Load the fixture catalog from a manifest file.

    :param manifest_path: path to the catalog manifest JSON.
    :return: the parsed catalog.
    """
    return FixtureCatalog.from_manifest(manifest_path)
