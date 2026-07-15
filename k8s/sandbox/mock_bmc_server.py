#!/usr/bin/env python3
"""Serve a captured Redfish corpus over a small HTTP endpoint."""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import logging
import os
import random
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_CORPUS_DIR = Path(
    os.environ.get("MOCK_BMC_CORPUS_DIR", "/corpus/gb300")
)
REST_API_STATUS_MAP_JSON = "rest_api_map.status.json"
LOGGER = logging.getLogger(__name__)


class MockBMCServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def _build_fixture_index(corpus_dir: Path) -> dict[str, Path]:
    """Index the corpus directory's JSON fixtures by lower-cased file name.

    :param corpus_dir: directory holding the flattened ``*.json`` fixtures.
    :return: mapping of lower-cased file name to its path.
    """
    root = Path(corpus_dir)
    return {path.name.lower(): path for path in root.glob("*.json")}


def _load_rest_api_map(corpus_dir: Path) -> dict[str, Any]:
    """Load the optional Redfish URL/status/error mapping.

    :param corpus_dir: directory that may contain a status JSON sidecar or
        legacy ``rest_api_map.npy``.
    :return: the decoded map, or an empty dict when the file is absent.
    :raises ValueError: if the map fails to load or does not decode to an
        object.
    """
    corpus_path = Path(corpus_dir)
    sidecar_path = corpus_path / REST_API_STATUS_MAP_JSON
    map_path = corpus_path / "rest_api_map.npy"
    if sidecar_path.exists():
        sidecar_map = _load_rest_api_map_json(sidecar_path)
        if not map_path.exists():
            return sidecar_map
        legacy_map = _load_rest_api_map_npy(map_path)
        merged = copy.deepcopy(legacy_map)
        for key in ("http_status_mapping", "error_file_mapping"):
            _log_status_sidecar_overrides(merged, sidecar_map, key, sidecar_path)
            merged[key] = sidecar_map[key]
        _validate_rest_api_map(merged, sidecar_path)
        return merged

    if not map_path.exists():
        return {}
    return _load_rest_api_map_npy(map_path)


def _load_rest_api_map_npy(map_path: Path) -> dict[str, Any]:
    """Load and validate a legacy NumPy Redfish API map.

    :param map_path: path to ``rest_api_map.npy``.
    :return: decoded and validated Redfish API map data.
    """
    try:
        import numpy as np
    except ModuleNotFoundError:
        raise ValueError(
            f"NumPy is required to load Redfish API map: {map_path}"
        ) from None
    try:
        data = np.load(map_path, allow_pickle=True).item()
    except Exception as exc:  # noqa: BLE001 - include loader context.
        raise ValueError(f"failed to load Redfish API map: {map_path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Redfish API map must contain an object: {map_path}")
    _validate_rest_api_map(data, map_path)
    return data


def _log_status_sidecar_overrides(
    legacy_map: dict[str, Any],
    sidecar_map: dict[str, Any],
    key: str,
    sidecar_path: Path,
) -> None:
    """Warn when a status sidecar replaces a legacy map value.

    :param legacy_map: legacy map loaded from ``rest_api_map.npy``.
    :param sidecar_map: status/error sidecar map.
    :param key: mapping section being overlaid.
    :param sidecar_path: sidecar path used in log context.
    """
    legacy_values = _mapping_dict(legacy_map, key)
    sidecar_values = _mapping_dict(sidecar_map, key)
    for request_path, sidecar_value in sidecar_values.items():
        if request_path not in legacy_values:
            continue
        legacy_value = legacy_values[request_path]
        if legacy_value == sidecar_value:
            continue
        LOGGER.warning(
            "%s overrides %s for %s: %r -> %r",
            sidecar_path.name,
            key,
            request_path,
            legacy_value,
            sidecar_value,
        )


def _load_rest_api_map_json(map_path: Path) -> dict[str, Any]:
    """Load a JSON Redfish API status/error map sidecar.

    :param map_path: sidecar file path to read and validate.
    :return: decoded and validated Redfish API map data.
    """
    try:
        data = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to load Redfish API map: {map_path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Redfish API map must contain an object: {map_path}")
    for key in ("http_status_mapping", "error_file_mapping"):
        if key not in data:
            raise ValueError(f"{key} is required in Redfish API map: {map_path}")
    _validate_rest_api_map(data, map_path)
    return data


def _validate_rest_api_map(api_map: dict[str, Any], map_path: Path) -> None:
    """Validate a loaded Redfish API map and normalize its status values.

    Coerces each ``http_status_mapping`` value to an int in place after
    checking the mapping sub-objects are dicts and the statuses are valid.

    :param api_map: the decoded API map to validate and normalize in place.
    :param map_path: source path, used only for error messages.
    :raises ValueError: if a mapping section is not an object or a status is
        not an integer in the 100-599 range.
    """
    for key in ("url_file_mapping", "http_status_mapping", "error_file_mapping"):
        value = api_map.get(key)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"{key} must be an object in Redfish API map: {map_path}")

    status_mapping = api_map.get("http_status_mapping")
    if not isinstance(status_mapping, dict):
        return
    for request_path, raw_status in list(status_mapping.items()):
        try:
            status = int(raw_status)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "http_status_mapping values must be integer HTTP statuses "
                f"in Redfish API map: {map_path} ({request_path!r})"
            ) from exc
        if status < 100 or status > 599:
            raise ValueError(
                "http_status_mapping values must be valid HTTP statuses "
                f"in Redfish API map: {map_path} ({request_path!r})"
            )
        status_mapping[request_path] = status


def _mapping_dict(api_map: dict[str, Any], key: str) -> dict[str, Any]:
    """Return one sub-mapping of the API map, or ``{}`` when absent.

    :param api_map: the decoded Redfish API map.
    :param key: sub-mapping name to fetch (e.g. ``url_file_mapping``).
    :return: the sub-mapping dict, or an empty dict when missing or not a dict.
    """
    value = api_map.get(key) or {}
    return value if isinstance(value, dict) else {}


def _candidate_map_keys(request_path: str) -> tuple[str, ...]:
    """Return the map keys to try for a request path, most specific first.

    The Redfish service root is looked up both with and without its trailing
    slash so either spelling in the map matches.

    :param request_path: the raw request path.
    :return: ordered tuple of candidate keys to look up.
    """
    path = _normalize_request_path(request_path)
    if path == "/redfish/v1":
        return (path, "/redfish/v1/")
    return (path,)


def _lookup_mapping(mapping: dict[str, Any], request_path: str) -> Any:
    """Return the mapping value for a request path's first matching key.

    :param mapping: a sub-mapping keyed by normalized request path.
    :param request_path: the request path to resolve.
    :return: the mapped value, or None when no candidate key matches.
    """
    for key in _candidate_map_keys(request_path):
        if key in mapping:
            return mapping[key]
    return None


def _resolve_mapped_fixture(corpus_dir: Path, mapped_name: Any) -> Path | None:
    """Resolve a mapped fixture name to a file contained in the corpus dir.

    Rejects non-string, empty, or absolute names and, via a containment
    guard, any name that resolves outside ``corpus_dir`` (e.g. ``../``).

    :param corpus_dir: the corpus root that must contain the resolved file.
    :param mapped_name: candidate file name from the API map.
    :return: the resolved file path, or None when it is invalid, escapes the
        corpus dir, or does not exist.
    """
    if not isinstance(mapped_name, str) or not mapped_name:
        return None
    root = Path(corpus_dir)
    mapped_path = Path(mapped_name)
    if mapped_path.is_absolute():
        return None
    root_resolved = root.resolve()
    candidates: list[Path] = [root / mapped_path, root / mapped_path.name]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        # Containment guard: a mapped name from rest_api_map with '..' segments
        # must never resolve outside the corpus dir. Without this, a crafted
        # url_file_mapping entry like "../../etc/passwd" would be served.
        if not resolved.is_relative_to(root_resolved):
            continue
        if resolved.is_file():
            return resolved
    return None


def _normalize_request_path(request_path: str) -> str:
    """Return the decoded URL path with any trailing slash stripped.

    Drops the query string, percent-decodes the path, and removes a trailing
    slash (except for the bare root ``/``).

    :param request_path: the raw request line path or a mapping key.
    :return: the normalized path.
    """
    path = unquote(urlsplit(request_path).path)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path


def fixture_for_redfish_path(corpus_dir: Path, request_path: str) -> Path | None:
    """Return the flattened corpus file for a Redfish request path.

    :param corpus_dir: directory holding the flattened fixtures.
    :param request_path: the Redfish request path to resolve.
    :return: the matching fixture path, or None for a non-Redfish path or a
        path with no flattened fixture.
    """
    path = _normalize_request_path(request_path)
    if not path.startswith("/redfish/v1"):
        return None

    key = "_" + path.strip("/").replace("/", "_") + ".json"
    return _build_fixture_index(Path(corpus_dir)).get(key.lower())


def _load_trace(trace_path: Path) -> dict[str, Any]:
    """Load a replay or mutation-rules trace file as a mapping.

    Parses the file as JSON and falls back to YAML when JSON fails and PyYAML
    is available.

    :param trace_path: path to the trace file to load.
    :return: the decoded trace object.
    :raises ValueError: if the content is not JSON, PyYAML is absent for a
        YAML file, or the decoded value is not an object.
    """
    text = Path(trace_path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ModuleNotFoundError as exc:
            raise ValueError(
                "replay trace must be JSON-compatible YAML when PyYAML is absent"
            ) from exc
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"replay trace must contain an object: {trace_path}")
    return data


def _body_contains(actual: Any, expected: Any) -> bool:
    """Recursively test whether ``expected`` is contained in ``actual``.

    Dicts match as a subset (every expected key/value present), lists match
    element-by-element over the expected length, and scalars match by equality.

    :param actual: the value taken from the request body.
    :param expected: the subset pattern to look for.
    :return: True if every part of ``expected`` is present in ``actual``.
    """
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _body_contains(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            return False
        return all(_body_contains(actual[i], value)
                   for i, value in enumerate(expected))
    return actual == expected


class _OverlayStore:
    """Shared corpus-overlay + state-transition machinery for write engines.

    Both the ordered ``ReplayState`` and the order-independent ``MutationRules``
    build the same kind of in-memory overlay: a per-resource dict that is
    deep-merged onto the corpus fixture at read time and mutated by
    ``state_transitions`` (``op`` = set/delete, ``path`` = resource,
    ``field``/``json_path`` = key, ``value``).
    """

    def __init__(self) -> None:
        """Initialize an empty overlay store guarded by a lock."""
        self._overlays: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def overlay_for(self, request_path: str) -> dict[str, Any]:
        """Return a deep copy of the accumulated overlay for a resource path.

        :param request_path: the resource path whose overlay is requested.
        :return: a private copy of the overlay, or ``{}`` when none exists.
        """
        path = _normalize_request_path(request_path)
        with self._lock:
            return copy.deepcopy(self._overlays.get(path, {}))

    def _apply_transitions(self, transitions: list[Any]) -> None:
        """Apply ``set``/``delete`` state transitions to the overlays.

        Each transition names a resource ``path`` and a ``field``/``json_path``;
        the caller must already hold ``self._lock``.

        :param transitions: transition dicts to apply in order; non-dicts and
            entries without a string ``path`` are skipped.
        """
        # Callers hold ``self._lock``; this only mutates ``self._overlays``.
        for transition in transitions:
            if not isinstance(transition, dict):
                continue
            op = transition.get("op")
            resource_path = transition.get("path")
            if not isinstance(resource_path, str):
                continue
            path = _normalize_request_path(resource_path)
            overlay = self._overlays.setdefault(path, {})
            if op == "set":
                keys = transition.get("json_path", transition.get("field"))
                self._set_value(overlay, keys, transition.get("value"))
            elif op == "delete":
                keys = transition.get("json_path", transition.get("field"))
                self._delete_value(overlay, keys)

    @staticmethod
    def _path_parts(keys: Any) -> list[str]:
        """Normalize a ``field``/``json_path`` spec into a list of key parts.

        :param keys: a single string key or a list of string keys.
        :return: the keys as a list.
        :raises ValueError: if ``keys`` is neither a string nor a list of strings.
        """
        if isinstance(keys, str):
            return [keys]
        if isinstance(keys, list) and all(isinstance(key, str) for key in keys):
            return keys
        raise ValueError("state transition needs field or json_path")

    @classmethod
    def _set_value(cls, data: dict[str, Any], keys: Any, value: Any) -> None:
        """Set a nested value in ``data``, creating intermediate dicts.

        Any non-dict node encountered along the path is replaced with a dict.

        :param data: the overlay dict mutated in place.
        :param keys: the field or json_path locating where to set the value.
        :param value: the value to store at the resolved location.
        """
        parts = cls._path_parts(keys)
        cursor = data
        for key in parts[:-1]:
            child = cursor.setdefault(key, {})
            if not isinstance(child, dict):
                child = {}
                cursor[key] = child
            cursor = child
        cursor[parts[-1]] = value

    @classmethod
    def _delete_value(cls, data: dict[str, Any], keys: Any) -> None:
        """Delete a nested key from ``data`` if present.

        Walks to the parent of the target key and removes it; a missing or
        non-dict node along the way makes this a no-op.

        :param data: the overlay dict mutated in place.
        :param keys: the field or json_path locating the key to remove.
        """
        parts = cls._path_parts(keys)
        cursor = data
        for key in parts[:-1]:
            child = cursor.get(key)
            if not isinstance(child, dict):
                return
            cursor = child
        cursor.pop(parts[-1], None)


class ReplayState(_OverlayStore):
    """Ordered write replay with in-memory state overlays for corpus reads."""

    def __init__(self, trace: dict[str, Any]) -> None:
        """Build replay state from a decoded trace.

        :param trace: decoded trace with a ``scenario`` and a ``steps`` list.
        :raises ValueError: if ``steps`` is present but not a list.
        """
        super().__init__()
        self.scenario = str(trace.get("scenario") or "default")
        steps = trace.get("steps") or []
        if not isinstance(steps, list):
            raise ValueError("replay trace steps must be a list")
        self.steps = steps
        self._matched: set[int] = set()

    @classmethod
    def from_file(cls, trace_path: Path) -> "ReplayState":
        """Load replay state from a trace file.

        :param trace_path: path to the replay trace file.
        :return: a new ``ReplayState`` built from the file.
        """
        return cls(_load_trace(trace_path))

    def status(self) -> dict[str, Any]:
        """Return a snapshot of replay progress.

        :return: dict with the scenario, matched/total step counts, the names
            of pending steps, and whether the trace is complete.
        """
        with self._lock:
            pending = [
                str(step.get("name") or f"step-{index}")
                for index, step in enumerate(self.steps)
                if index not in self._matched
            ]
            matched_steps = len(self._matched)
        return {
            "scenario": self.scenario,
            "matched_steps": matched_steps,
            "pending_steps": pending,
            "total_steps": len(self.steps),
            "complete": not pending,
        }

    def reset(self, scenario: str | None = None) -> bool:
        """Clear matched steps and overlays, restarting the replay.

        :param scenario: if given, only reset when it names this trace's
            scenario; otherwise reset unconditionally.
        :return: True if reset, False when ``scenario`` names another scenario.
        """
        if scenario is not None and scenario != self.scenario:
            return False
        with self._lock:
            self._matched.clear()
            self._overlays.clear()
        return True

    def match_write(
        self,
        method: str,
        request_path: str,
        body: Any,
        corpus_state: Any = None,
    ) -> dict[str, Any] | None:
        """Match a write against the next pending step and apply its effects.

        Only the next unmatched step is considered, so writes must arrive in
        the trace's order.

        :param method: HTTP method of the write.
        :param request_path: target resource path.
        :param body: parsed request body.
        :param corpus_state: accepted for interface parity with
            :class:`MutationRules`; not used by this engine.
        :return: the step's response dict (defaulting to ``{"status": 204}``),
            or None when the next step does not match.
        """
        path = _normalize_request_path(request_path)
        with self._lock:
            next_index = self._next_pending_index()
            if next_index is None:
                return None
            step = self.steps[next_index]
            if not self._matches(step, method, path, body):
                return None
            self._matched.add(next_index)
            self._apply_transitions(step.get("state_transitions") or [])
            response = step.get("response") or {}
            return response if isinstance(response, dict) else {"status": 204}

    def _next_pending_index(self) -> int | None:
        """Return the index of the first unmatched step.

        :return: the earliest pending step index, or None when all matched.
        """
        for index in range(len(self.steps)):
            if index not in self._matched:
                return index
        return None

    @staticmethod
    def _matches(
        step: dict[str, Any],
        method: str,
        path: str,
        body: Any,
    ) -> bool:
        """Whether a step matches a write's method, path, and body.

        :param step: the trace step to test.
        :param method: HTTP method of the write.
        :param path: normalized request path.
        :param body: parsed request body, compared against the step's ``body``
            and/or ``body_contains`` constraints.
        :return: True if the step matches the write.
        """
        if str(step.get("method", "")).upper() != method.upper():
            return False
        if _normalize_request_path(str(step.get("path", ""))) != path:
            return False
        if "body" in step and body != step["body"]:
            return False
        if "body_contains" in step and not _body_contains(
            body, step["body_contains"]
        ):
            return False
        return True


class MutationRules(_OverlayStore):
    """Order-independent write rules keyed on (method, path, precondition).

    Where ``ReplayState`` accepts only the next step of a fixed trace, every
    rule here is evaluated against each write, so a controller may drive the
    same mutations in any order (and repeatedly). A rule fires when the method
    and normalized path match, its optional ``body_contains`` subset matches,
    and every ``when`` precondition holds against the CURRENT effective state
    (corpus fixture + overlays already applied). All matching rules apply their
    ``state_transitions``; the first match's ``response`` is returned. This lets
    conditional side effects compose — e.g. a reset both powers the system off
    and, only when ``BootSourceOverrideEnabled == Once``, reverts the one-shot
    boot.
    """

    def __init__(self, spec: dict[str, Any], *, seed: int = 0) -> None:
        """Build mutation rules from a decoded spec.

        :param spec: decoded spec with a ``vendor`` and a ``rules`` list.
        :param seed: RNG seed for reproducible stochastic failure injection.
        :raises ValueError: if ``rules`` is present but not a list.
        """
        super().__init__()
        self.vendor = str(spec.get("vendor") or "generic")
        rules = spec.get("rules") or []
        if not isinstance(rules, list):
            raise ValueError("mutation rules must be a list")
        self.rules = rules
        self._applied: list[str] = []
        self._failed: list[str] = []
        # Seeded RNG so stochastic failure injection is reproducible: the same
        # seed replays the same sequence of injected failures (an RL harness
        # varies the seed per episode). reset() re-seeds to the same value.
        self._seed = int(seed)
        self._rng = random.Random(self._seed)

    @classmethod
    def from_file(cls, path: Path, *, seed: int = 0) -> "MutationRules":
        """Load mutation rules from a spec file.

        :param path: path to the mutation-rules file.
        :param seed: RNG seed for reproducible failure injection.
        :return: a new ``MutationRules`` built from the file.
        """
        return cls(_load_trace(path), seed=seed)

    def status(self) -> dict[str, Any]:
        """Return a snapshot of mutation-rules activity.

        :return: dict with the mode, vendor, rule count, the names of applied
            and failed rules, and the RNG seed.
        """
        with self._lock:
            return {
                "mode": "mutation-rules",
                "vendor": self.vendor,
                "total_rules": len(self.rules),
                "applied": list(self._applied),
                "failed": list(self._failed),
                "seed": self._seed,
            }

    def reset(self, scenario: str | None = None) -> bool:
        """Clear overlays and history and re-seed the RNG.

        :param scenario: if given, only reset when it names this spec's vendor;
            otherwise reset unconditionally.
        :return: True if reset, False when ``scenario`` names another vendor.
        """
        if scenario is not None and scenario != self.vendor:
            return False
        with self._lock:
            self._overlays.clear()
            self._applied.clear()
            self._failed.clear()
            self._rng = random.Random(self._seed)
        return True

    def match_write(
        self,
        method: str,
        request_path: str,
        body: Any,
        corpus_state: Any = None,
    ) -> dict[str, Any] | None:
        """Evaluate every rule against a write and apply the matches.

        Candidate rules are shape-matched (method/path/body) outside the lock
        and their precondition corpus is pre-read; under the lock the matching
        rules' preconditions are checked, optional failure injection is rolled,
        and all matches' transitions are applied.

        :param method: HTTP method of the write.
        :param request_path: target resource path.
        :param body: parsed request body.
        :param corpus_state: callable mapping a resource path to its corpus
            fixture, used to evaluate rule preconditions; a non-callable value
            disables precondition corpus reads.
        :return: the first matching rule's response (or an injected failure
            response), or None when no rule matches.
        """
        path = _normalize_request_path(request_path)
        corpus_lookup = corpus_state if callable(corpus_state) else (lambda _p: {})
        # Shape-match rules (method/path/body) OUTSIDE the lock, then pre-read the
        # corpus state their preconditions need. The corpus is immutable and
        # independent of engine state, so this file read + JSON parse must NOT run
        # under self._lock — doing so serializes every concurrent writer (and
        # overlay reader) on disk I/O. Under the lock we evaluate only these
        # candidates' preconditions against the pre-read snapshot: minimal work,
        # zero I/O.
        candidates = [
            rule
            for rule in self.rules
            if isinstance(rule, dict)
            and self._rule_request_matches(rule, method, path, body)
        ]
        corpus_cache = self._load_precondition_corpus(candidates, corpus_lookup)
        cached_lookup = self._cached_corpus_lookup(corpus_cache)
        with self._lock:
            matched = [
                rule
                for rule in candidates
                if self._rule_preconditions_hold(rule, cached_lookup)
            ]
            if not matched:
                return None
            # Stochastic failure injection: if a matched rule rolls a failure the
            # whole write fails and no transitions apply (models e.g. a flaky
            # reboot). Rules with no failure block never touch the RNG, so purely
            # deterministic rules stay deterministic regardless of seed.
            failure_response = self._roll_failure(matched)
            if failure_response is not None:
                return failure_response
            for rule in matched:
                self._apply_transitions(rule.get("state_transitions") or [])
                self._applied.append(str(rule.get("name") or "unnamed"))
            response = matched[0].get("response") or {}
            return response if isinstance(response, dict) else {"status": 204}

    @staticmethod
    def _precondition_paths(rules: list[dict[str, Any]]) -> set[str]:
        """Normalized resource paths every candidate rule's preconditions read.

        :param rules: candidate rules whose ``when`` blocks are scanned.
        :return: the set of normalized resource paths referenced by preconditions.
        """
        paths: set[str] = set()
        for rule in rules:
            for condition in rule.get("when") or []:
                if not isinstance(condition, dict):
                    continue
                resource_path = condition.get("path")
                if isinstance(resource_path, str):
                    paths.add(_normalize_request_path(resource_path))
        return paths

    def _load_precondition_corpus(
        self,
        rules: list[dict[str, Any]],
        corpus_lookup: Any,
    ) -> dict[str, Any]:
        """Pre-read (outside the lock) the corpus snapshot the candidates need.

        Deep-copied at store time so the snapshot is a private copy immune to any
        later mutation of what ``corpus_lookup`` returned — at no lock-time cost.

        :param rules: candidate rules whose precondition paths are pre-read.
        :param corpus_lookup: callable returning the corpus fixture for a path.
        :return: mapping of each precondition path to a deep-copied snapshot.
        """
        return {
            path: copy.deepcopy(corpus_lookup(path) or {})
            for path in self._precondition_paths(rules)
        }

    @staticmethod
    def _cached_corpus_lookup(corpus_cache: dict[str, Any]) -> Any:
        """Serve pre-read corpus state under the lock without touching disk.

        No deep copy here: the only consumer, ``_effective_state``, already
        deep-copies before mutating, so a second copy would be dead work under the
        lock. A miss returns ``{}`` (impossible in practice — the cache covers
        every literal ``when`` path) so the locked section stays strictly I/O-free.

        :param corpus_cache: pre-read snapshots keyed by normalized path.
        :return: a callable resolving a resource path to its cached snapshot.
        """

        def lookup(resource_path: str) -> Any:
            """Return the cached snapshot for a resource path (``{}`` on miss).

            :param resource_path: the resource path to resolve.
            :return: the cached snapshot, or ``{}`` when not cached.
            """
            return corpus_cache.get(_normalize_request_path(resource_path), {})

        return lookup

    def _roll_failure(self, matched: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Roll stochastic failure injection for the matched rules.

        Each matched rule with a ``failure`` block is rolled against its
        probability; the first hit records the rule as failed and returns its
        failure response. Rules without a failure block never touch the RNG.

        :param matched: rules whose preconditions already hold.
        :return: the failure response of the first rule that rolls a failure,
            or None when no failure fires.
        """
        for rule in matched:
            failure = rule.get("failure")
            if not isinstance(failure, dict):
                continue
            try:
                probability = float(failure.get("probability", 0) or 0)
            except (TypeError, ValueError):
                probability = 0.0
            if probability <= 0:
                continue
            if self._rng.random() < probability:
                self._failed.append(str(rule.get("name") or "unnamed"))
                response = failure.get("response") or {"status": 500}
                return response if isinstance(response, dict) else {"status": 500}
        return None

    def _effective_state(self, resource_path: str, corpus_lookup: Any) -> dict[str, Any]:
        """Return the current effective state of a resource for preconditions.

        Merges the pre-read corpus fixture with any overlay already applied by
        earlier writes.

        :param resource_path: the resource whose state is needed.
        :param corpus_lookup: callable returning the corpus fixture for a path.
        :return: a deep-copied merge of the fixture and the current overlay.
        """
        path = _normalize_request_path(resource_path)
        base = copy.deepcopy(corpus_lookup(path) or {})
        overlay = self._overlays.get(path)
        if overlay:
            _deep_update(base, overlay)
        return base

    def _rule_request_matches(
        self,
        rule: dict[str, Any],
        method: str,
        path: str,
        body: Any,
    ) -> bool:
        """Match a rule on method/path/body only — no ``when`` / corpus lookup.

        Pure and lock-free: depends only on the immutable rule and the request,
        never on overlays or corpus files, so candidate selection and the corpus
        pre-read run before ``self._lock``.

        :param rule: the rule to test.
        :param method: HTTP method of the write.
        :param path: normalized request path (matched literally or by glob).
        :param body: parsed request body, checked against ``body``/``body_contains``.
        :return: True if the rule's shape matches the request.
        """
        if str(rule.get("method", "")).upper() != method.upper():
            return False
        rule_path = _normalize_request_path(str(rule.get("path", "")))
        if any(ch in rule_path for ch in "*?["):
            # A glob path-pattern matches a family of resources (e.g. every
            # VirtualMedia slot) in one rule.
            if not fnmatch.fnmatchcase(path, rule_path):
                return False
        elif rule_path != path:
            return False
        if "body" in rule and body != rule["body"]:
            return False
        if "body_contains" in rule and not _body_contains(body, rule["body_contains"]):
            return False
        return True

    def _rule_preconditions_hold(self, rule: dict[str, Any], corpus_lookup: Any) -> bool:
        """Whether every ``when`` precondition holds against the pre-read state.

        :param rule: the rule whose ``when`` conditions are evaluated.
        :param corpus_lookup: callable returning the pre-read corpus snapshot.
        :return: True if every precondition holds (vacuously true when none).
        """
        for condition in rule.get("when") or []:
            if not isinstance(condition, dict):
                return False
            if not self._precondition_holds(condition, corpus_lookup):
                return False
        return True

    def _precondition_holds(self, condition: dict[str, Any], corpus_lookup: Any) -> bool:
        """Evaluate one ``when`` precondition against the effective state.

        Resolves the condition's ``field``/``json_path`` in the resource's
        effective state and applies its ``exists``, ``absent``, ``equals``, or
        ``not_equals`` test.

        :param condition: the precondition to evaluate.
        :param corpus_lookup: callable returning the pre-read corpus snapshot.
        :return: True if the condition holds; False on a malformed condition or
            a missing value where one is required.
        """
        resource_path = condition.get("path")
        if not isinstance(resource_path, str):
            return False
        state = self._effective_state(resource_path, corpus_lookup)
        keys = self._path_parts(condition.get("json_path", condition.get("field")))
        cursor: Any = state
        present = True
        for key in keys:
            if isinstance(cursor, dict) and key in cursor:
                cursor = cursor[key]
            else:
                present = False
                break
        if "exists" in condition:
            return present == bool(condition["exists"])
        if "absent" in condition:
            return present == (not bool(condition["absent"]))
        if not present:
            return False
        if "equals" in condition:
            return cursor == condition["equals"]
        if "not_equals" in condition:
            return cursor != condition["not_equals"]
        return present


class CorpusRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves a flattened Redfish corpus."""

    server_version = "redfish-ctl-mock-bmc/1.0"

    def _fixture_for_request(self) -> Path | None:
        """Resolve the corpus fixture for the current request.

        Prefers an ``url_file_mapping`` entry from the API map, then falls back
        to the flattened ``_redfish_v1_*.json`` file-name index.

        :return: the fixture path, or None for a non-Redfish path or one with
            no matching fixture.
        """
        fixture_index = getattr(type(self), "fixture_index")
        api_map = getattr(type(self), "api_map", {})
        corpus_dir = getattr(type(self), "corpus_dir")
        path = _normalize_request_path(self.path)
        if not path.startswith("/redfish/v1"):
            return None
        mapped_name = _lookup_mapping(
            _mapping_dict(api_map, "url_file_mapping"),
            path,
        )
        mapped_fixture = _resolve_mapped_fixture(corpus_dir, mapped_name)
        if mapped_fixture is not None:
            return mapped_fixture
        key = "_" + path.strip("/").replace("/", "_") + ".json"
        return fixture_index.get(key.lower())

    def _captured_error_for_request(self) -> tuple[int, Path | None] | None:
        """Return a captured error response for the current request, if any.

        Consults the API map's ``http_status_mapping`` and ``error_file_mapping``
        so previously captured error responses are replayed.

        :return: a ``(status, error_fixture_or_None)`` pair when an error is
            recorded for the path, or None when the request should be served
            normally.
        """
        api_map = getattr(type(self), "api_map", {})
        if not api_map:
            return None
        path = _normalize_request_path(self.path)
        if not path.startswith("/redfish/v1"):
            return None
        raw_status = _lookup_mapping(
            _mapping_dict(api_map, "http_status_mapping"),
            path,
        )
        status = int(raw_status) if raw_status is not None else None
        error_fixture = _resolve_mapped_fixture(
            getattr(type(self), "corpus_dir"),
            _lookup_mapping(_mapping_dict(api_map, "error_file_mapping"), path),
        )
        if error_fixture is None:
            if status is None or status < 400:
                return None
            return status, None
        return status or 500, error_fixture

    def _active_writer(self) -> "ReplayState | MutationRules | None":
        """The write engine in effect (mutation-rules takes precedence).

        :return: the active ``MutationRules`` or ``ReplayState``, or None when
            no write engine is configured.
        """
        return getattr(type(self), "mutation_rules", None) or getattr(
            type(self), "replay_state", None
        )

    def _corpus_state(self, request_path: str) -> dict[str, Any]:
        """Corpus fixture (+identity overlay) for a resource, for preconditions.

        Deliberately excludes the write engine's own overlay: the engine holds
        its lock and merges that overlay itself, so returning it here would
        double-apply it (and risk re-entrancy on the engine lock).

        :param request_path: the resource path whose corpus state is needed.
        :return: the fixture merged with any identity overlay, or ``{}`` for a
            non-Redfish path or a path with no fixture.
        """
        fixture_index = getattr(type(self), "fixture_index")
        path = _normalize_request_path(request_path)
        if not path.startswith("/redfish/v1"):
            return {}
        key = "_" + path.strip("/").replace("/", "_") + ".json"
        fixture = fixture_index.get(key.lower())
        if fixture is None:
            return {}
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        identity = getattr(type(self), "identity_overlay", {})
        identity_overlay = identity.get(path.lower(), {})
        if identity_overlay:
            _deep_update(payload, identity_overlay)
        return payload

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        """Send a JSON response with the given status and payload.

        :param status: HTTP status code to send.
        :param payload: JSON-serializable body, sent sorted by key.
        """
        content = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_empty(self, status: int) -> None:
        """Send an empty response with the given status.

        :param status: HTTP status code to send with a zero-length body.
        """
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_fixture(self, send_body: bool) -> None:
        """Serve the fixture (or captured error) for the current request.

        Replays a captured error when the API map records one, otherwise sends
        the resolved fixture with any identity and write-engine overlays applied,
        or 404 when no fixture matches.

        :param send_body: whether to write the response body (False for HEAD).
        """
        captured_error = self._captured_error_for_request()
        if captured_error is not None:
            status, error_fixture = captured_error
            if error_fixture is None:
                content = json.dumps(
                    {"error": f"captured status {status} for {self.path}"},
                    sort_keys=True,
                ).encode("utf-8")
            else:
                content = error_fixture.read_bytes()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if send_body:
                self.wfile.write(content)
            return

        fixture = self._fixture_for_request()
        if fixture is None:
            self._send_json(404, {"error": f"no fixture for {self.path}"})
            return

        writer = self._active_writer()
        replay_overlay = writer.overlay_for(self.path) if writer is not None else {}
        identity = getattr(type(self), "identity_overlay", {})
        identity_overlay = identity.get(
            _normalize_request_path(self.path).lower(), {}
        )
        if replay_overlay or identity_overlay:
            payload = json.loads(fixture.read_text(encoding="utf-8"))
            if identity_overlay:
                _deep_update(payload, identity_overlay)
            if replay_overlay:
                _deep_update(payload, replay_overlay)
            content = json.dumps(payload, sort_keys=True).encode("utf-8")
        else:
            content = fixture.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if send_body:
            self.wfile.write(content)

    def do_GET(self) -> None:
        """Handle a GET request.

        Serves the ``/__replay_status`` endpoint when a write engine is active,
        otherwise serves the corpus fixture for the path.
        """
        if _normalize_request_path(self.path) == "/__replay_status":
            writer = self._active_writer()
            if writer is None:
                self._send_json(404, {"error": "replay is not enabled"})
                return
            self._send_json(200, writer.status())
            return
        self._serve_fixture(send_body=True)

    def do_HEAD(self) -> None:
        """Handle a HEAD request by serving fixture headers without a body."""
        self._serve_fixture(send_body=False)

    def do_OPTIONS(self) -> None:
        """Handle an OPTIONS request by advertising the allowed methods.

        Write methods are advertised only when a write engine is active.
        """
        self.send_response(204)
        allow = "GET, HEAD, OPTIONS"
        if self._active_writer() is not None:
            allow = f"{allow}, POST, PATCH, PUT, DELETE"
        self.send_header("Allow", allow)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        """Handle a POST request.

        Serves the ``/__set_scenario`` control endpoint, otherwise dispatches
        the write to the active engine.
        """
        if _normalize_request_path(self.path) == "/__set_scenario":
            self._handle_set_scenario()
            return
        self._handle_replay_write("POST")

    def do_PATCH(self) -> None:
        """Handle a PATCH request by dispatching it to the active write engine."""
        self._handle_replay_write("PATCH")

    def do_PUT(self) -> None:
        """Handle a PUT request by dispatching it to the active write engine."""
        self._handle_replay_write("PUT")

    def do_DELETE(self) -> None:
        """Handle a DELETE request by dispatching it to the active write engine."""
        self._handle_replay_write("DELETE")

    def _read_json_body(self) -> Any:
        """Read and JSON-decode the request body.

        :return: the decoded body, or ``{}`` when the request has no body.
        """
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        content = self.rfile.read(length).decode("utf-8")
        return json.loads(content) if content else {}

    def _handle_set_scenario(self) -> None:
        """Handle the ``/__set_scenario`` control endpoint.

        Resets the active write engine to the requested scenario, replying 404
        when no engine is active or the scenario is unknown and 400 when the
        scenario is not a string.
        """
        writer = self._active_writer()
        if writer is None:
            self._send_json(404, {"error": "replay is not enabled"})
            return
        body = self._read_json_body()
        scenario = body.get("scenario") if isinstance(body, dict) else None
        if scenario is not None and not isinstance(scenario, str):
            self._send_json(400, {"error": "scenario must be a string"})
            return
        if not writer.reset(scenario):
            self._send_json(404, {"error": f"unknown scenario {scenario}"})
            return
        self._send_json(200, writer.status())

    def _handle_replay_write(self, method: str) -> None:
        """Dispatch a write to the active engine and send its response.

        Replies 405 when no engine is active and 409 when no rule or step
        matches; otherwise sends the engine's response status and body.

        :param method: HTTP method of the write being handled.
        """
        writer = self._active_writer()
        if writer is None:
            self._send_method_not_allowed()
            return
        body = self._read_json_body()
        response = writer.match_write(method, self.path, body, self._corpus_state)
        if response is None:
            self._send_json(
                409,
                {
                    "error": "no write rule matched the request",
                    "method": method,
                    "path": _normalize_request_path(self.path),
                    "status": writer.status(),
                },
            )
            return
        status = int(response.get("status", 204))
        payload = response.get("body")
        if payload is None or status == 204:
            self._send_empty(status)
            return
        if not isinstance(payload, dict):
            payload = {"result": payload}
        self._send_json(status, payload)

    def _send_method_not_allowed(self) -> None:
        """Send a 405 response advertising the read-only method set."""
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.send_header("Content-Type", "application/json")
        content = b'{"error": "read-only mock BMC"}'
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        """Log a request line only when ``MOCK_BMC_VERBOSE`` is ``1``.

        :param format: printf-style format string from the base handler.
        """
        if os.environ.get("MOCK_BMC_VERBOSE") == "1":
            super().log_message(format, *args)


def _deep_update(target: dict[str, Any], overlay: dict[str, Any]) -> None:
    """Recursively merge ``overlay`` into ``target`` in place.

    Nested dicts are merged key by key; any other value replaces the target's.

    :param target: the mapping mutated in place.
    :param overlay: the values to merge on top of ``target``.
    """
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def identity_overlay_from_env() -> dict[str, dict[str, Any]]:
    """Per-pod identity overlay from ``MOCK_BMC_RACK`` / ``MOCK_BMC_SLOT``.

    Lets one committed corpus image serve as many DISTINCT BMCs: each pod
    overlays its own serial and name onto the System and Manager resources so
    inventory reads report a unique node (``GB300-R<rack>-S<slot>``). Only
    non-structural fields are overlaid — never ``Id`` or ``@odata.id``, which
    the link-walk depends on. Returns an empty overlay when neither env var is
    set, so a single-node sandbox is unchanged.

    :return: mapping of resource path to the fields to overlay, empty when
        neither ``MOCK_BMC_RACK`` nor ``MOCK_BMC_SLOT`` is set.
    """
    rack = os.environ.get("MOCK_BMC_RACK", "").strip()
    slot = os.environ.get("MOCK_BMC_SLOT", "").strip()
    if not rack and not slot:
        return {}
    node = f"GB300-R{rack or '0'}-S{(slot or '0').zfill(2)}"
    return {
        "/redfish/v1/systems/system_0": {"SerialNumber": node, "Name": node},
        "/redfish/v1/managers/bmc_0": {
            "SerialNumber": node,
            "Name": f"{node}-BMC",
        },
    }


def make_handler(
    corpus_dir: Path,
    replay_trace: Path | None = None,
    mutation_rules: Path | None = None,
    seed: int = 0,
) -> type[CorpusRequestHandler]:
    """Build a request-handler class bound to a corpus and optional write engine.

    Loads the corpus fixtures and API map onto a fresh handler subclass and
    attaches at most one write engine (replay trace or mutation rules) plus any
    per-pod identity overlay.

    :param corpus_dir: directory of flattened Redfish fixtures to serve.
    :param replay_trace: optional ordered write-replay trace file.
    :param mutation_rules: optional order-independent mutation-rules file.
    :param seed: RNG seed for mutation-rules failure injection.
    :return: a ``CorpusRequestHandler`` subclass configured for the corpus.
    :raises FileNotFoundError: if ``corpus_dir`` is not a directory.
    :raises ValueError: if both write engines are given or no fixtures exist.
    """
    root = Path(corpus_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"corpus directory not found: {root}")
    if replay_trace is not None and mutation_rules is not None:
        raise ValueError("choose either --replay or --mutation-rules, not both")

    class Handler(CorpusRequestHandler):
        pass

    Handler.corpus_dir = root
    Handler.api_map = _load_rest_api_map(root)
    Handler.fixture_index = _build_fixture_index(root)
    if not Handler.fixture_index:
        raise ValueError(f"no JSON fixtures found under {root}")
    Handler.replay_state = (
        ReplayState.from_file(replay_trace)
        if replay_trace is not None
        else None
    )
    Handler.mutation_rules = (
        MutationRules.from_file(mutation_rules, seed=seed)
        if mutation_rules is not None
        else None
    )
    Handler.identity_overlay = identity_overlay_from_env()
    return Handler


@contextmanager
def run_server(
    host: str,
    port: int,
    corpus_dir: Path,
    replay_trace: Path | None = None,
    mutation_rules: Path | None = None,
    seed: int = 0,
) -> Iterator[ThreadingHTTPServer]:
    """Run the mock BMC server in a background thread as a context manager.

    Starts the server on a daemon thread, yields it for the duration of the
    ``with`` block, then shuts it down and joins the thread on exit.

    :param host: interface address to bind.
    :param port: TCP port to bind.
    :param corpus_dir: directory of flattened Redfish fixtures to serve.
    :param replay_trace: optional ordered write-replay trace file.
    :param mutation_rules: optional order-independent mutation-rules file.
    :param seed: RNG seed for mutation-rules failure injection.
    """
    server = MockBMCServer(
        (host, port),
        make_handler(
            corpus_dir,
            replay_trace=replay_trace,
            mutation_rules=mutation_rules,
            seed=seed,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the mock BMC server command-line arguments.

    :param argv: argument vector to parse; None reads ``sys.argv``.
    :return: the parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description="Serve a flattened Redfish JSON corpus over read-only HTTP."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=DEFAULT_CORPUS_DIR,
        help="Directory containing flattened _redfish_v1*.json files.",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=None,
        help="Optional ordered write-replay trace file (fixed step sequence).",
    )
    parser.add_argument(
        "--mutation-rules",
        type=Path,
        default=None,
        help="Optional order-independent mutation-rules file "
        "(matches on method/path/precondition; applies writes in any order).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=int(os.environ.get("MOCK_BMC_SEED", "0") or "0"),
        help="Seed for mutation-rules failure injection (default 0, "
        "overridable via MOCK_BMC_SEED). Same seed replays the same failures.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the mock BMC server until interrupted.

    Parses arguments, starts the server, and serves requests until Ctrl-C.

    :param argv: argument vector to parse; None reads ``sys.argv``.
    :return: process exit code — 2 on a corpus/config error, else 0.
    """
    args = parse_args(argv)
    server = None
    try:
        server = MockBMCServer(
            (args.host, args.port),
            make_handler(
                args.corpus_dir,
                replay_trace=args.replay,
                mutation_rules=args.mutation_rules,
                seed=args.seed,
            ),
        )
        host, port = server.server_address
        print(f"Serving Redfish corpus from {args.corpus_dir} on {host}:{port}")
        server.serve_forever()
    except (FileNotFoundError, ValueError) as exc:
        print(f"mock-bmc: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    finally:
        if server is not None:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
