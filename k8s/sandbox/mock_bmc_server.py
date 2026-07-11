#!/usr/bin/env python3
"""Serve a captured Redfish corpus over a small HTTP endpoint."""

from __future__ import annotations

import argparse
import copy
import json
import os
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
    os.environ.get("MOCK_BMC_CORPUS_DIR", "/corpus/172.25.230.37")
)


def _build_fixture_index(corpus_dir: Path) -> dict[str, Path]:
    root = Path(corpus_dir)
    return {path.name.lower(): path for path in root.glob("*.json")}


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


class ReplayState:
    """Ordered write replay with in-memory state overlays for corpus reads."""

    def __init__(self, trace: dict[str, Any]) -> None:
        self.scenario = str(trace.get("scenario") or "default")
        steps = trace.get("steps") or []
        if not isinstance(steps, list):
            raise ValueError("replay trace steps must be a list")
        self.steps = steps
        self._matched: set[int] = set()
        self._overlays: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

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

    def overlay_for(self, request_path: str) -> dict[str, Any]:
        path = _normalize_request_path(request_path)
        with self._lock:
            return copy.deepcopy(self._overlays.get(path, {}))

    def match_write(
        self,
        method: str,
        request_path: str,
        body: Any,
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

    def _apply_transitions(self, transitions: list[Any]) -> None:
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


class CorpusRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves a flattened Redfish corpus."""

    server_version = "redfish-ctl-mock-bmc/1.0"

    def _fixture_for_request(self) -> Path | None:
        fixture_index = getattr(type(self), "fixture_index")
        path = _normalize_request_path(self.path)
        if not path.startswith("/redfish/v1"):
            return None
        key = "_" + path.strip("/").replace("/", "_") + ".json"
        return fixture_index.get(key.lower())

    def _replay_state(self) -> ReplayState | None:
        return getattr(type(self), "replay_state", None)

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
        fixture = self._fixture_for_request()
        if fixture is None:
            self._send_json(404, {"error": f"no fixture for {self.path}"})
            return

        replay = self._replay_state()
        replay_overlay = replay.overlay_for(self.path) if replay is not None else {}
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
            replay = self._replay_state()
            if replay is None:
                self._send_json(404, {"error": "replay is not enabled"})
                return
            self._send_json(200, replay.status())
            return
        self._serve_fixture(send_body=True)

    def do_HEAD(self) -> None:
        self._serve_fixture(send_body=False)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        allow = "GET, HEAD, OPTIONS"
        if self._replay_state() is not None:
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
        replay = self._replay_state()
        if replay is None:
            self._send_json(404, {"error": "replay is not enabled"})
            return
        body = self._read_json_body()
        scenario = body.get("scenario") if isinstance(body, dict) else None
        if scenario is not None and not isinstance(scenario, str):
            self._send_json(400, {"error": "scenario must be a string"})
            return
        if not replay.reset(scenario):
            self._send_json(404, {"error": f"unknown scenario {scenario}"})
            return
        self._send_json(200, replay.status())

    def _handle_replay_write(self, method: str) -> None:
        replay = self._replay_state()
        if replay is None:
            self._send_method_not_allowed()
            return
        body = self._read_json_body()
        response = replay.match_write(method, self.path, body)
        if response is None:
            self._send_json(
                409,
                {
                    "error": "request did not match the next replay step",
                    "method": method,
                    "path": _normalize_request_path(self.path),
                    "status": replay.status(),
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
) -> type[CorpusRequestHandler]:
    root = Path(corpus_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"corpus directory not found: {root}")

    class Handler(CorpusRequestHandler):
        pass

    Handler.fixture_index = _build_fixture_index(root)
    if not Handler.fixture_index:
        raise ValueError(f"no JSON fixtures found under {root}")
    Handler.replay_state = (
        ReplayState.from_file(replay_trace)
        if replay_trace is not None
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
) -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(corpus_dir, replay_trace=replay_trace),
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
        help="Optional write replay trace file.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    server = None
    try:
        server = ThreadingHTTPServer(
            (args.host, args.port),
            make_handler(args.corpus_dir, replay_trace=args.replay),
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
