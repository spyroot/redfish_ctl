#!/usr/bin/env python3
"""Serve a captured Redfish corpus over a small read-only HTTP endpoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlsplit

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_CORPUS_DIR = Path(
    os.environ.get("MOCK_BMC_CORPUS_DIR", "/corpus/172.25.230.37")
)


def _build_fixture_index(corpus_dir: Path) -> dict[str, Path]:
    root = Path(corpus_dir)
    return {path.name.lower(): path for path in root.glob("*.json")}


def fixture_for_redfish_path(corpus_dir: Path, request_path: str) -> Path | None:
    """Return the flattened corpus file for a Redfish request path."""
    path = unquote(urlsplit(request_path).path)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if not path.startswith("/redfish/v1"):
        return None

    key = "_" + path.strip("/").replace("/", "_") + ".json"
    return _build_fixture_index(Path(corpus_dir)).get(key.lower())


class CorpusRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves GET and HEAD from a flattened Redfish corpus."""

    server_version = "redfish-ctl-mock-bmc/1.0"

    def _fixture_for_request(self) -> Path | None:
        fixture_index = getattr(type(self), "fixture_index")
        path = unquote(urlsplit(self.path).path)
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        if not path.startswith("/redfish/v1"):
            return None
        key = "_" + path.strip("/").replace("/", "_") + ".json"
        return fixture_index.get(key.lower())

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        content = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_fixture(self, send_body: bool) -> None:
        fixture = self._fixture_for_request()
        if fixture is None:
            self._send_json(404, {"error": f"no fixture for {self.path}"})
            return

        content = fixture.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if send_body:
            self.wfile.write(content)

    def do_GET(self) -> None:
        self._serve_fixture(send_body=True)

    def do_HEAD(self) -> None:
        self._serve_fixture(send_body=False)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        self._send_method_not_allowed()

    def do_PATCH(self) -> None:
        self._send_method_not_allowed()

    def do_PUT(self) -> None:
        self._send_method_not_allowed()

    def do_DELETE(self) -> None:
        self._send_method_not_allowed()

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


def make_handler(corpus_dir: Path) -> type[CorpusRequestHandler]:
    root = Path(corpus_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"corpus directory not found: {root}")

    class Handler(CorpusRequestHandler):
        pass

    Handler.fixture_index = _build_fixture_index(root)
    if not Handler.fixture_index:
        raise ValueError(f"no JSON fixtures found under {root}")
    return Handler


@contextmanager
def run_server(
    host: str,
    port: int,
    corpus_dir: Path,
) -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer((host, port), make_handler(corpus_dir))
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    server = None
    try:
        server = ThreadingHTTPServer(
            (args.host, args.port),
            make_handler(args.corpus_dir),
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
