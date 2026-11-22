"""Vendor-generic comparison engine: a Redfish corpus vs a live BMC (or itself).

Compares STABLE identity/config fields (Manufacturer, Model, firmware versions,
boot allowable values, the BIOS attribute KEY SET) between two Redfish trees and
ignores volatile state (power, sensor readings, timestamps). Resources are
DISCOVERED, never hardcoded: the ServiceRoot links name the Systems/Managers
collections and their ``Members`` lists name the per-vendor member ids, so the
same engine walks a Dell tree (``System.Embedded.1``), an HPE tree
(``Systems/1``), or a Supermicro tree (``System_0``) without per-vendor code.

Both sides of the comparison are plain fetchers (``path -> dict | None``): the
corpus side reads flattened ``_redfish_v1_*.json`` fixtures from an extracted
corpus directory, and the live side is injected by the caller (see
``tools/corpus.py live-diff``, which routes through the ``redfish_ctl`` client).
Feeding the SAME corpus fetcher to both sides is the offline self-check that
proves the discovery is vendor-generic.

Row statuses: ``match`` / ``drift`` (a stable field differs — the failure
signal) / ``live_gap`` (live side unreachable or lacks the resource) /
``corpus_gap`` (the capture never included the resource) / ``not_present`` (a
field absent on BOTH sides — never observed, so it counts as neither verified
nor drifted). Gaps are reported but do not fail the comparison; only drift
does. A comparison that verified NOTHING (``checked == 0``) is an error the
CLI maps to its usage/environment exit code, never a pass.

Consumed by ``tools/corpus.py`` (subcommands ``live-diff`` and ``self-check``)
and ``tests/test_corpus_diff.py``.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

SERVICE_ROOT = "/redfish/v1"

# Stable fields compared per resource kind. The nesting separator is "/" — a
# character that cannot appear in a Redfish property name — because property
# names themselves may contain dots (``...@Redfish.AllowableValues``). A
# trailing "#keys" compares the KEY SET of a dict (the BIOS attribute names)
# instead of its volatile values.
SERVICE_ROOT_FIELDS = ("Vendor", "Product", "RedfishVersion")
SYSTEM_FIELDS = ("Manufacturer", "Model", "BiosVersion",
                 "Boot/BootSourceOverrideTarget@Redfish.AllowableValues")
BIOS_FIELDS = ("Attributes#keys",)
MANAGER_FIELDS = ("Manufacturer", "Model", "FirmwareVersion", "ManagerType")

# Unique absence sentinel: a real Redfish value can never be identical to this
# object, so "field missing" can never collide with (or match) a string value.
_ABSENT = object()

# Bounded output: cap list diffs and value reprs so a huge BIOS registry can
# never turn one drift row into a multi-kilobyte dump.
_DETAIL_ITEMS = 5
_DETAIL_CHARS = 120

Fetcher = Callable[[str], Optional[dict]]


def fixture_name(path: str) -> str:
    """Return the flattened corpus filename for a Redfish resource path.

    Mirrors the crawl layout served by the mock BMC server: every ``/`` becomes
    ``_`` and the file carries a ``.json`` suffix.

    :param path: Redfish resource path (e.g. ``/redfish/v1/Systems``).
    :return: flattened fixture filename (e.g. ``_redfish_v1_Systems.json``).
    """
    return "_" + path.strip("/").replace("/", "_") + ".json"


def corpus_fetcher(corpus_dir: Path) -> Fetcher:
    """Return a fetcher reading flattened fixtures from an extracted corpus.

    The directory is indexed ONCE, case-insensitively, so repeated lookups are
    dict hits and a vendor's odd path casing cannot miss its own fixture.

    :param corpus_dir: directory of flattened ``_redfish_v1_*.json`` files.
    :return: fetcher mapping a resource path to its parsed fixture dict or None.
    """
    files = sorted(Path(corpus_dir).glob("*.json"))
    index: dict[str, Path] = {}
    collisions = []
    for p in files:
        key = p.name.lower()
        if key in index:
            collisions.append(f"{index[key].name} <-> {p.name}")
        index[key] = p
    if collisions:
        # Two fixtures differing only by case would shadow each other in a
        # filesystem-order-dependent way — refuse loudly instead of guessing.
        raise ValueError(
            f"corpus {corpus_dir} has case-colliding fixtures: "
            + "; ".join(collisions[:_DETAIL_ITEMS]))

    def fetch(path: str) -> Optional[dict]:
        """Read one flattened fixture for ``path``, or None when not captured.

        :param path: Redfish resource path.
        :return: parsed fixture dict, or None when the corpus lacks the file.
        """
        hit = index.get(fixture_name(path).lower())
        if hit is None:
            return None
        try:
            data = json.loads(hit.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    return fetch


def _dig(obj, field_path: str):
    """Follow a ``/``-separated key path through nested dicts.

    The separator is ``/`` because Redfish property names may themselves
    contain dots (``BootSourceOverrideTarget@Redfish.AllowableValues``).

    :param obj: root object (typically a parsed Redfish resource).
    :param field_path: ``/``-separated key path (e.g. ``Boot/BootSource...``).
    :return: the value at the path, or the unique :data:`_ABSENT` sentinel.
    """
    cur = obj
    for part in field_path.split("/"):
        if not isinstance(cur, dict) or part not in cur:
            return _ABSENT
        cur = cur[part]
    return cur


def _clip(value) -> str:
    """Render a value for a report row within the bounded-output budget.

    :param value: any comparison value (:data:`_ABSENT` renders as ``<absent>``).
    :return: ``repr`` of the value, truncated to the detail budget.
    """
    text = "<absent>" if value is _ABSENT else repr(value)
    return text if len(text) <= _DETAIL_CHARS else text[:_DETAIL_CHARS] + "..."


def _row(path: str, field: str, status: str, detail: str) -> dict:
    """Build one report row.

    :param path: Redfish resource path the row describes.
    :param field: compared field name (or a pseudo-field like ``Members#ids``).
    :param status: ``match`` | ``drift`` | ``live_gap`` | ``corpus_gap``.
    :param detail: bounded human-readable detail line.
    :return: the row dict.
    """
    return {"path": path, "field": field, "status": status, "detail": detail}


def _compare_field(path: str, field: str, live: dict, corpus: dict) -> dict:
    """Compare one stable field between the live and corpus resources.

    :param path: resource path (for the report row).
    :param field: dotted field name; a ``#keys`` suffix compares a dict's key set.
    :param live: live-side resource dict.
    :param corpus: corpus-side resource dict.
    :return: a ``match``/``drift`` report row.
    """
    if field.endswith("#keys"):
        base = field[: -len("#keys")]
        lv, cv = _dig(live, base), _dig(corpus, base)
        lk = set(lv.keys()) if isinstance(lv, dict) else set()
        ck = set(cv.keys()) if isinstance(cv, dict) else set()
        if lk == ck:
            return _row(path, field, "match", f"{base}: {len(lk)} keys match")
        return _row(path, field, "drift",
                    f"{base}: live_only={sorted(lk - ck)[:_DETAIL_ITEMS]} "
                    f"corpus_only={sorted(ck - lk)[:_DETAIL_ITEMS]}")
    lv, cv = _dig(live, field), _dig(corpus, field)
    if lv is _ABSENT and cv is _ABSENT:
        # Never observed on either side: not verified, so it must not count as
        # a match (that would inflate the verified-field count).
        return _row(path, field, "not_present", "absent on both sides")
    status = "match" if lv == cv else "drift"
    return _row(path, field, status, f"live={_clip(lv)} corpus={_clip(cv)}")


def _members(resource: Optional[dict]) -> list[str]:
    """Return the sorted ``Members`` ``@odata.id`` list of a collection.

    :param resource: a Redfish collection resource, or None.
    :return: sorted member resource paths (empty when absent/malformed).
    """
    if not isinstance(resource, dict):
        return []
    out = []
    for member in resource.get("Members", []):
        if isinstance(member, dict) and isinstance(member.get("@odata.id"), str):
            out.append(member["@odata.id"].rstrip("/"))
    return sorted(out)


def _link(resource: Optional[dict], key: str) -> Optional[str]:
    """Return a linked resource path (``resource[key]["@odata.id"]``).

    :param resource: a Redfish resource dict, or None.
    :param key: linked property name (e.g. ``Systems``, ``Bios``).
    :return: the linked path, or None when the link is absent.
    """
    if not isinstance(resource, dict):
        return None
    link = resource.get(key)
    if isinstance(link, dict) and isinstance(link.get("@odata.id"), str):
        return link["@odata.id"].rstrip("/")
    return None


def _compare_resource(path: str, fields: tuple[str, ...],
                      live_fetch: Fetcher, corpus_fetch: Fetcher,
                      rows: list[dict]) -> Optional[dict]:
    """Fetch one resource on both sides and compare its stable fields.

    Appends result rows in place; a missing side yields a single gap row.

    :param path: resource path to fetch on both sides.
    :param fields: stable fields to compare when both sides are present.
    :param live_fetch: live-side fetcher.
    :param corpus_fetch: corpus-side fetcher.
    :param rows: report row list, appended in place.
    :return: the corpus-side resource (for follow-up link discovery), or None.
    """
    corpus = corpus_fetch(path)
    if corpus is None:
        rows.append(_row(path, "-", "corpus_gap", "not captured in the corpus"))
        return None
    live = live_fetch(path)
    if live is None:
        rows.append(_row(path, "-", "live_gap", "unreachable / not served live"))
        return corpus
    for field in fields:
        rows.append(_compare_field(path, field, live, corpus))
    return corpus


def plan(corpus_fetch: Fetcher) -> list[str]:
    """Return the resource paths a comparison WOULD fetch (the dry-run plan).

    Discovery runs on the corpus side only — no live I/O happens.

    :param corpus_fetch: corpus-side fetcher.
    :return: ordered resource paths the comparison would touch.
    """
    root = corpus_fetch(SERVICE_ROOT)
    if root is None:
        # No ServiceRoot => nothing is discoverable; an empty plan is the
        # loud signal (the CLI maps it to its usage/environment exit).
        return []
    paths = [SERVICE_ROOT]
    for coll_key in ("Systems", "Managers"):
        coll_path = _link(root, coll_key) or f"{SERVICE_ROOT}/{coll_key}"
        paths.append(coll_path)
        coll = corpus_fetch(coll_path)
        for member_path in _members(coll):
            paths.append(member_path)
            if coll_key == "Systems":
                bios = _link(corpus_fetch(member_path), "Bios")
                if bios:
                    paths.append(bios)
    return paths


def compare(live_fetch: Fetcher, corpus_fetch: Fetcher) -> dict:
    """Walk the discovered tree and compare every stable field.

    The corpus is the reference: discovery (collection links, member ids, the
    Bios link) is driven from the corpus side, and the live side is fetched for
    the same paths. Differing member-id SETS are drift (hardware/config
    changed); a resource missing on one side is a gap, not drift.

    :param live_fetch: live-side fetcher (a real BMC via ``redfish_ctl``, the
        mock server, or the corpus itself for a self-check).
    :param corpus_fetch: corpus-side fetcher (see :func:`corpus_fetcher`).
    :return: report dict with ``rows`` and a ``summary`` (``ok`` = no drift).
    """
    rows: list[dict] = []
    root = _compare_resource(SERVICE_ROOT, SERVICE_ROOT_FIELDS,
                             live_fetch, corpus_fetch, rows)
    member_fields = {"Systems": SYSTEM_FIELDS, "Managers": MANAGER_FIELDS}
    for coll_key, fields in member_fields.items():
        coll_path = _link(root, coll_key) or f"{SERVICE_ROOT}/{coll_key}"
        corpus_coll = corpus_fetch(coll_path)
        if corpus_coll is None:
            rows.append(_row(coll_path, "-", "corpus_gap",
                             "collection not captured in the corpus"))
            continue
        live_coll = live_fetch(coll_path)
        if live_coll is None:
            rows.append(_row(coll_path, "-", "live_gap",
                             "collection unreachable / not served live"))
            continue
        for side, coll in (("live", live_coll), ("corpus", corpus_coll)):
            if isinstance(coll, dict) and "Members@odata.nextLink" in coll:
                # A paginated collection means page one is NOT the full member
                # set — the comparison would be silently incomplete, so flag it
                # loudly (the CLI refuses a green exit on any paginated row).
                rows.append(_row(coll_path, "Members@odata.nextLink", "paginated",
                                 f"{side} collection is paginated; only page one "
                                 "was compared"))
        live_ids, corpus_ids = set(_members(live_coll)), set(_members(corpus_coll))
        if live_ids == corpus_ids:
            rows.append(_row(coll_path, "Members#ids", "match",
                             f"{len(corpus_ids)} members match"))
        else:
            rows.append(_row(
                coll_path, "Members#ids", "drift",
                f"live_only={sorted(live_ids - corpus_ids)[:_DETAIL_ITEMS]} "
                f"corpus_only={sorted(corpus_ids - live_ids)[:_DETAIL_ITEMS]}"))
        for member_path in sorted(corpus_ids & live_ids):
            member = _compare_resource(member_path, fields,
                                       live_fetch, corpus_fetch, rows)
            if coll_key == "Systems":
                bios_path = _link(member, "Bios")
                if bios_path:
                    _compare_resource(bios_path, BIOS_FIELDS,
                                      live_fetch, corpus_fetch, rows)
    drift = sum(r["status"] == "drift" for r in rows)
    gaps = sum(r["status"] in ("live_gap", "corpus_gap") for r in rows)
    matched = sum(r["status"] == "match" for r in rows)
    not_present = sum(r["status"] == "not_present" for r in rows)
    paginated = sum(r["status"] == "paginated" for r in rows)
    return {
        "rows": rows,
        "summary": {"checked": matched + drift, "matched": matched,
                    "drift": drift, "gaps": gaps,
                    "not_present": not_present, "paginated": paginated,
                    "ok": drift == 0 and paginated == 0},
    }
