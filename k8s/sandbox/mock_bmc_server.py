#!/usr/bin/env python3
"""Serve a captured Redfish corpus over a small HTTP endpoint."""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
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


class MockBMCServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def _build_fixture_index(corpus_dir: Path) -> dict[str, Path]:
    root = Path(corpus_dir)
    return {path.name.lower(): path for path in root.glob("*.json")}


def _load_rest_api_map(corpus_dir: Path) -> dict[str, Any]:
    map_path = Path(corpus_dir) / "rest_api_map.npy"
    if not map_path.exists():
        return {}
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


def _validate_rest_api_map(api_map: dict[str, Any], map_path: Path) -> None:
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
    value = api_map.get(key) or {}
    return value if isinstance(value, dict) else {}


def _candidate_map_keys(request_path: str) -> tuple[str, ...]:
    path = _normalize_request_path(request_path)
    if path == "/redfish/v1":
        return (path, "/redfish/v1/")
    return (path,)


def _lookup_mapping(mapping: dict[str, Any], request_path: str) -> Any:
    for key in _candidate_map_keys(request_path):
        if key in mapping:
            return mapping[key]
    return None


def _resolve_mapped_fixture(corpus_dir: Path, mapped_name: Any) -> Path | None:
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
    path = unquote(urlsplit(request_path).path)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path


def fixture_for_redfish_path(corpus_dir: Path, request_path: str) -> Path | None:
    """Return the flattened corpus file for a Redfish request path."""
    path = _normalize_request_path(request_path)
    if not path.startswith("/redfish/v1"):
        return None

    key = "_" + path.strip("/").replace("/", "_") + ".json"
    return _build_fixture_index(Path(corpus_dir)).get(key.lower())


def _load_trace(trace_path: Path) -> dict[str, Any]:
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
        self._overlays: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def overlay_for(self, request_path: str) -> dict[str, Any]:
        path = _normalize_request_path(request_path)
        with self._lock:
            return copy.deepcopy(self._overlays.get(path, {}))

    def _apply_transitions(self, transitions: list[Any]) -> None:
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
        if isinstance(keys, str):
            return [keys]
        if isinstance(keys, list) and all(isinstance(key, str) for key in keys):
            return keys
        raise ValueError("state transition needs field or json_path")

    @classmethod
    def _set_value(cls, data: dict[str, Any], keys: Any, value: Any) -> None:
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
        super().__init__()
        self.scenario = str(trace.get("scenario") or "default")
        steps = trace.get("steps") or []
        if not isinstance(steps, list):
            raise ValueError("replay trace steps must be a list")
        self.steps = steps
        self._matched: set[int] = set()

    @classmethod
    def from_file(cls, trace_path: Path) -> "ReplayState":
        return cls(_load_trace(trace_path))

    def status(self) -> dict[str, Any]:
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
        return cls(_load_trace(path), seed=seed)

    def status(self) -> dict[str, Any]:
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
        """Normalized resource paths every candidate rule's preconditions read."""
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
        """

        def lookup(resource_path: str) -> Any:
            return corpus_cache.get(_normalize_request_path(resource_path), {})

        return lookup

    def _roll_failure(self, matched: list[dict[str, Any]]) -> dict[str, Any] | None:
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
        """Whether every ``when`` precondition holds against the pre-read state."""
        for condition in rule.get("when") or []:
            if not isinstance(condition, dict):
                return False
            if not self._precondition_holds(condition, corpus_lookup):
                return False
        return True

    def _precondition_holds(self, condition: dict[str, Any], corpus_lookup: Any) -> bool:
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
        """The write engine in effect (mutation-rules takes precedence)."""
        return getattr(type(self), "mutation_rules", None) or getattr(
            type(self), "replay_state", None
        )

    def _corpus_state(self, request_path: str) -> dict[str, Any]:
        """Corpus fixture (+identity overlay) for a resource, for preconditions.

        Deliberately excludes the write engine's own overlay: the engine holds
        its lock and merges that overlay itself, so returning it here would
        double-apply it (and risk re-entrancy on the engine lock).
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
        content = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_fixture(self, send_body: bool) -> None:
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
        if _normalize_request_path(self.path) == "/__replay_status":
            writer = self._active_writer()
            if writer is None:
                self._send_json(404, {"error": "replay is not enabled"})
                return
            self._send_json(200, writer.status())
            return
        self._serve_fixture(send_body=True)

    def do_HEAD(self) -> None:
        self._serve_fixture(send_body=False)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        allow = "GET, HEAD, OPTIONS"
        if self._active_writer() is not None:
            allow = f"{allow}, POST, PATCH, PUT, DELETE"
        self.send_header("Allow", allow)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        if _normalize_request_path(self.path) == "/__set_scenario":
            self._handle_set_scenario()
            return
        self._handle_replay_write("POST")

    def do_PATCH(self) -> None:
        self._handle_replay_write("PATCH")

    def do_PUT(self) -> None:
        self._handle_replay_write("PUT")

    def do_DELETE(self) -> None:
        self._handle_replay_write("DELETE")

    def _read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        content = self.rfile.read(length).decode("utf-8")
        return json.loads(content) if content else {}

    def _handle_set_scenario(self) -> None:
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
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.send_header("Content-Type", "application/json")
        content = b'{"error": "read-only mock BMC"}'
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        if os.environ.get("MOCK_BMC_VERBOSE") == "1":
            super().log_message(format, *args)


def _deep_update(target: dict[str, Any], overlay: dict[str, Any]) -> None:
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
