#!/usr/bin/env python3
"""Check the mock server's DSP2043 mockup mode against the bundle itself.

The DSP2043 bundle (``spec/dmtf/redfish/2026.1/mockups/DSP2043_2026.1.zip``,
Git-LFS) is the ground truth: every ``@odata.id`` a profile document declares is
a URI the mock server (``k8s/sandbox/mock_bmc_server.py --mockup-dir``) must
serve 1:1 from ``<profile-root>/<path>/index.json``, and a declared URI with no
backing file must 404 — the mock must never invent data. This tool extracts
that contract from the bundle and interrogates a locally spawned mock over
HTTP:

    python3 tools/mockup_consistency_check.py                       # every profile
    python3 tools/mockup_consistency_check.py --profile public-telemetry
    python3 tools/mockup_consistency_check.py --bundle <zip> --profile all

Per profile the report counts ``matched`` (HTTP 200, parsed JSON equal to the
bundle file), ``mismatched`` (bounded detail: uri, status, first divergent key
path), ``absent_declared`` (declared URIs with no bundle file that the mock
correctly 404s), and ``coverage.files_never_referenced`` (bundle files no
``@odata.id`` points to — informational orphans, never a failure). The DSP0266
section 6.7 special URIs are covered: ``/redfish`` (a synthesized
``{"v1": "/redfish/v1/"}`` counts as matched), ``/redfish/v1/$metadata``
(XML, compared as bytes), and service-root trailing-slash equivalence.

The machine-readable sim contract (``specs/sim/dmtf-sim-contract.yaml``) names
this tool as its conformance proof (``binding.conformance.proof``); the
guarantees rows and DSP0266 6.7 URIs it proves are declared in
:data:`CONTRACT_GUARANTEES_PROVEN` and :data:`SPEC_URIS_COVERED`, reconciled
against the contract by ``tests/k8s/test_mockup_consistency.py``.

Audience: agent | human. One JSON report on stdout, diagnostics on stderr.
Exit 0 = consistent, 1 = at least one mismatch, 2 = usage/environment error
(bare LFS pointer, unknown profile, a server without ``--mockup-dir``).
``--help`` works offline; the tool only ever talks to 127.0.0.1.
"""
from __future__ import annotations

import argparse
import atexit
import collections
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = REPO_ROOT / "spec" / "dmtf" / "redfish" / "2026.1" / "mockups" / (
    "DSP2043_2026.1.zip"
)
SERVER_SCRIPT = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"
SERVICE_ROOT = "/redfish/v1"
READY_DEADLINE_SECONDS = 15.0
HTTP_TIMEOUT_SECONDS = 10.0
_VALUE_REPR_LIMIT = 80

#: Guarantees rows of ``specs/sim/dmtf-sim-contract.yaml`` this proof enforces:
#: ``fidelity`` (JSON parsed-equal, ``$metadata`` byte-equal) and
#: ``no_invention`` (absent from the bundle means 404, with the ``/redfish``
#: version object as the single sanctioned synthesis).
CONTRACT_GUARANTEES_PROVEN = ("fidelity", "no_invention")

#: DSP0266 1.24.0 section 6.7 spec-defined URIs the checker exercises
#: explicitly on every profile (the contract's required ``spec_uris`` rows).
SPEC_URIS_COVERED = (
    "/redfish",
    "/redfish/v1/",
    "/redfish/v1/odata",
    "/redfish/v1/$metadata",
)

_EXTRACT_CACHE: dict[str, Path] = {}


class ToolError(Exception):
    """A usage or environment failure that maps to exit code 2.

    :param message: what failed, concrete enough to act on.
    :param next_step: the safe next step that unblocks the caller.
    """

    def __init__(self, message: str, next_step: str) -> None:
        """Store the failure text and its recommended next step.

        :param message: what failed, concrete enough to act on.
        :param next_step: the safe next step that unblocks the caller.
        """
        super().__init__(message)
        self.next_step = next_step


def _diag(message: str) -> None:
    """Write one diagnostic line to stderr (stdout carries only the report).

    :param message: the line to emit.
    :return: None.
    """
    print(message, file=sys.stderr)


def _is_lfs_pointer(path: Path) -> bool:
    """True if ``path`` is still a bare Git-LFS pointer (same predicate as
    ``tools/corpus.py``, duplicated to keep this tool import-free).

    :param path: file to probe.
    :return: True when the file starts with the Git-LFS pointer preamble.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(120)
    except OSError:
        return False
    return head.startswith(b"version https://git-lfs.github.com/spec")


def _extract_bundle(bundle: Path) -> Path:
    """Extract ``bundle`` once per process into a temp dir cleaned at exit.

    :param bundle: path to the DSP2043 ``.zip``.
    :return: the bundle root directory (the single top-level dir when the
        archive wraps everything in one, else the extraction dir itself).
    :raises ToolError: when the file is missing, a bare LFS pointer, or not a
        readable zip archive.
    """
    key = str(bundle)
    if key in _EXTRACT_CACHE:
        return _EXTRACT_CACHE[key]
    if not bundle.is_file():
        raise ToolError(
            f"bundle not found: {bundle}",
            "pass --bundle <path-to-DSP2043 zip> or restore the spec/ tree",
        )
    if _is_lfs_pointer(bundle):
        raise ToolError(
            f"bundle is a bare Git-LFS pointer: {bundle}",
            "run: git lfs pull --include='spec/dmtf/redfish/*/mockups/*.zip'",
        )
    tmp = Path(tempfile.mkdtemp(prefix="dsp2043_check_"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    try:
        with zipfile.ZipFile(bundle) as archive:
            for name in archive.namelist():
                if name.startswith(("/", "..")) or ".." in Path(name).parts:
                    raise ToolError(
                        f"bundle entry escapes the extraction dir: {name}",
                        "refuse this archive; re-download the DSP2043 bundle",
                    )
            archive.extractall(tmp)
    except zipfile.BadZipFile as exc:
        raise ToolError(
            f"bundle is not a readable zip ({exc}): {bundle}",
            "re-download the DSP2043 bundle or re-run git lfs pull",
        ) from exc
    children = list(tmp.iterdir())
    root = children[0] if len(children) == 1 and children[0].is_dir() else tmp
    _EXTRACT_CACHE[key] = root
    return root


def _list_profiles(bundle_root: Path) -> list[str]:
    """Name every mockup profile in the bundle.

    A profile is a top-level directory carrying a service-root ``index.json``
    either directly at its root or under a ``redfish/v1/`` prefix (the two
    layouts the mock server's ``--mockup-dir`` mode resolves).

    :param bundle_root: extracted bundle root directory.
    :return: sorted profile directory names.
    """
    return sorted(
        child.name
        for child in bundle_root.iterdir()
        if child.is_dir()
        and (
            (child / "index.json").is_file()
            or (child / "redfish" / "v1" / "index.json").is_file()
        )
    )


def _service_root_dir(profile_dir: Path) -> Path:
    """Resolve the profile directory that maps to ``/redfish/v1``.

    Mirrors the mock server's ``mockup_service_root``: a ``redfish/v1/``
    prefixed layout wins when present, else the profile root itself is the
    service root (the DSP2043 bundle's own layout).

    :param profile_dir: the profile root directory.
    :return: the directory whose ``index.json`` is the service root document.
    """
    prefixed = profile_dir / "redfish" / "v1"
    if (prefixed / "index.json").is_file():
        return prefixed
    return profile_dir


def _normalize_uri(value: Any) -> str | None:
    """Normalize one ``@odata.id`` value into a checkable Redfish URI.

    Strips a URL fragment (``#/...``) and any trailing slash, and keeps only
    URIs under ``/redfish``.

    :param value: the raw ``@odata.id`` value from a bundle document.
    :return: the normalized URI, or None when the value is not a string or
        not under ``/redfish``.
    """
    if not isinstance(value, str):
        return None
    uri = value.split("#", 1)[0]
    if len(uri) > 1:
        uri = uri.rstrip("/") or "/"
    if not uri.startswith("/redfish"):
        return None
    return uri


def _collect_odata_ids(node: Any, out: set[str]) -> None:
    """Recursively collect every normalized ``@odata.id`` under ``node``.

    :param node: any decoded JSON value (dict, list, or scalar).
    :param out: set the normalized URIs are added to.
    :return: None.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "@odata.id":
                uri = _normalize_uri(value)
                if uri is not None:
                    out.add(uri)
            _collect_odata_ids(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_odata_ids(item, out)


def _declared_uris(profile_dir: Path) -> set[str]:
    """Extract the profile's declared URI set from every ``*.json`` document.

    Documents that fail to parse are reported on stderr and skipped; they
    still surface through the per-URI check when an ``index.json`` is the
    unparseable file.

    :param profile_dir: the profile root directory.
    :return: deduplicated set of normalized ``@odata.id`` URIs.
    """
    declared: set[str] = set()
    for path in sorted(profile_dir.rglob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _diag(f"warning: skipping unparseable JSON {path}: {exc}")
            continue
        _collect_odata_ids(document, declared)
    return declared


def _file_uris(service_root: Path) -> dict[str, Path]:
    """Map every ``index.json``-backed URI in the profile to its file.

    The service root maps to ``/redfish/v1`` and each subdirectory ``X/Y``
    holding an ``index.json`` maps to ``/redfish/v1/X/Y`` (the mock's 1:1
    rule).

    :param service_root: the directory that maps to ``/redfish/v1``.
    :return: mapping of URI to the backing ``index.json`` path.
    """
    uris: dict[str, Path] = {}
    for path in sorted(service_root.rglob("index.json")):
        relative = path.parent.relative_to(service_root)
        if relative == Path("."):
            uris[SERVICE_ROOT] = path
        else:
            uris[f"{SERVICE_ROOT}/{relative.as_posix()}"] = path
    return uris


def _direct_file(service_root: Path, uri: str) -> Path | None:
    """Resolve a declared URI to a verbatim (non-``index.json``) bundle file.

    Mirrors the mock server's last resolution candidate: when a URI under
    ``/redfish/v1`` has no ``index.json`` but names a real file in the tree
    (e.g. ``openapi.yaml``), that file is served byte-faithful.

    :param service_root: the directory that maps to ``/redfish/v1``.
    :param uri: the declared URI to resolve.
    :return: the direct file path, or None when the URI has no such file.
    """
    if not uri.startswith(f"{SERVICE_ROOT}/"):
        return None
    subpath = uri[len(SERVICE_ROOT):].strip("/")
    if not subpath or ".." in Path(subpath).parts:
        return None
    candidate = service_root / subpath
    return candidate if candidate.is_file() else None


def _first_divergence(expected: Any, actual: Any, path: str = "$") -> str | None:
    """Locate the first key path where two decoded JSON values diverge.

    Dict keys are visited in sorted order so the answer is deterministic.

    :param expected: the bundle-side value.
    :param actual: the mock-side value.
    :param path: dotted key path accumulated so far.
    :return: a one-line description of the first divergence, or None when the
        values are equal.
    """
    if expected == actual:
        return None
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                return f"{path}.{key}: missing from the mock response"
            if key not in expected:
                return f"{path}.{key}: extra key in the mock response"
            sub = _first_divergence(expected[key], actual[key], f"{path}.{key}")
            if sub is not None:
                return sub
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{path}: list length {len(expected)} != {len(actual)}"
        for index, (left, right) in enumerate(zip(expected, actual)):
            sub = _first_divergence(left, right, f"{path}[{index}]")
            if sub is not None:
                return sub
    left_repr = repr(expected)[:_VALUE_REPR_LIMIT]
    right_repr = repr(actual)[:_VALUE_REPR_LIMIT]
    return f"{path}: {left_repr} != {right_repr}"


def _free_port() -> int:
    """Ask the OS for a currently free localhost TCP port.

    :return: the port number (released again before the server starts, so a
        parallel process could theoretically race it; runs are sequential).
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _require_mockup_dir_support() -> None:
    """Fail fast when the mock server does not offer ``--mockup-dir``.

    :return: None.
    :raises ToolError: when the server script is missing or its ``--help``
        does not mention the flag.
    """
    if not SERVER_SCRIPT.is_file():
        raise ToolError(
            f"mock server script not found: {SERVER_SCRIPT}",
            "run from a full checkout that carries k8s/sandbox/mock_bmc_server.py",
        )
    result = subprocess.run(
        [sys.executable, str(SERVER_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if "--mockup-dir" not in result.stdout + result.stderr:
        raise ToolError(
            "the mock server on this checkout does not support --mockup-dir",
            "update k8s/sandbox/mock_bmc_server.py to a revision that provides "
            "the DSP2043 mockup mode, then re-run",
        )


@contextmanager
def _mock_server(serve_dir: Path) -> Iterator[str]:
    """Run the mock server on ``serve_dir`` for the duration of the block.

    The child's stdout/stderr are drained into a bounded ring buffer so a
    chatty request log can never fill the pipe and stall the server; the tail
    is surfaced when startup fails.

    :param serve_dir: the profile root directory handed to ``--mockup-dir``.
    :return: iterator yielding the ``http://127.0.0.1:<port>`` base URL.
    :raises ToolError: when the server exits or never becomes ready.
    """
    port = _free_port()
    command = [
        sys.executable,
        str(SERVER_SCRIPT),
        "--mockup-dir",
        str(serve_dir),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output_tail: collections.deque[str] = collections.deque(maxlen=50)

    def _drain() -> None:
        """Consume the child's merged output into the bounded tail buffer.

        :return: None.
        """
        assert process.stdout is not None
        for line in process.stdout:
            output_tail.append(line.rstrip("\n"))

    threading.Thread(target=_drain, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(process, base, output_tail)
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _wait_ready(
    process: subprocess.Popen,
    base: str,
    output_tail: collections.deque[str],
) -> None:
    """Block until the spawned mock answers HTTP or the deadline passes.

    Any HTTP status counts as ready — only connection refusal keeps waiting.

    :param process: the mock server child process.
    :param base: its ``http://127.0.0.1:<port>`` base URL.
    :param output_tail: ring buffer of the child's recent output lines.
    :return: None.
    :raises ToolError: when the child exits early or never answers.
    """
    deadline = time.monotonic() + READY_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ToolError(
                "mock server exited during startup "
                f"(rc={process.returncode}): {' | '.join(output_tail)}",
                "run the printed server command by hand to see the failure",
            )
        try:
            urllib.request.urlopen(base + SERVICE_ROOT, timeout=2).close()
            return
        except urllib.error.HTTPError:
            return
        except OSError:
            time.sleep(0.1)
    raise ToolError(
        f"mock server never answered on {base} within "
        f"{READY_DEADLINE_SECONDS:.0f}s: {' | '.join(output_tail)}",
        "check the server output above; the port may be firewalled",
    )


def _http_get(base: str, uri: str) -> tuple[int, bytes]:
    """GET one URI from the mock and return the raw outcome.

    :param base: the mock's base URL.
    :param uri: the absolute Redfish URI to fetch.
    :return: tuple of (HTTP status, response body bytes).
    :raises ToolError: when the connection itself fails.
    """
    url = base + urllib.parse.quote(uri, safe="/$&+,;=:@'()*!._~-")
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except OSError as exc:
        raise ToolError(
            f"GET {uri} failed at the connection level: {exc}",
            "re-run; if it repeats, the mock server is crashing mid-run",
        ) from exc


def _load_json_bytes(payload: bytes) -> tuple[Any, str | None]:
    """Decode a response body as JSON.

    :param payload: raw response bytes.
    :return: tuple of (decoded value or None, error text or None).
    """
    try:
        return json.loads(payload.decode("utf-8")), None
    except (UnicodeDecodeError, ValueError) as exc:
        return None, f"mock body is not JSON: {exc}"


def _check_special_uris(
    base: str,
    profile_dir: Path,
    service_root: Path,
    root_file: Path | None,
    mismatched: list[dict[str, Any]],
) -> tuple[int, int]:
    """Check the DSP0266 section 6.7 special URIs against the mock.

    Covers ``/redfish`` (the version object — the profile's own
    ``redfish/index.json`` when shipped, else the synthesized
    ``{"v1": "/redfish/v1/"}``), ``/redfish/v1/$metadata`` (XML,
    byte-compared when the bundle carries it), ``/redfish/v1/odata``
    (JSON-equal when the profile ships it, a clean 404 when it does not —
    many bundle profiles never reference it by ``@odata.id``, so without
    this explicit probe it would go unchecked), and trailing-slash
    equivalence on the service root.

    :param base: the mock's base URL.
    :param profile_dir: the profile root directory in the bundle extraction.
    :param service_root: the directory that maps to ``/redfish/v1``.
    :param root_file: the profile's service-root ``index.json``, when present.
    :param mismatched: mismatch list new findings are appended to.
    :return: tuple of (URIs checked, URIs matched).
    """
    checked = 0
    matched = 0

    checked += 1
    version_file = profile_dir / "redfish" / "index.json"
    expected_version: Any = {"v1": "/redfish/v1/"}
    if version_file.is_file():
        expected_version = json.loads(version_file.read_text(encoding="utf-8"))
    status, body = _http_get(base, "/redfish")
    version_doc, error = _load_json_bytes(body)
    if status == 200 and error is None and version_doc == expected_version:
        matched += 1
    else:
        mismatched.append(
            {
                "uri": "/redfish",
                "status": status,
                "divergence": error
                or 'expected 200 with the version object {"v1": "/redfish/v1/"} '
                "(DSP0266 6.7)",
            }
        )

    metadata_file = service_root / "$metadata" / "index.xml"
    if metadata_file.is_file():
        checked += 1
        status, body = _http_get(base, f"{SERVICE_ROOT}/$metadata")
        expected_xml = metadata_file.read_bytes()
        if status == 200 and body == expected_xml:
            matched += 1
        else:
            divergence = f"expected 200 with the bundle XML, got status {status}"
            if status == 200:
                offset = next(
                    (i for i, (a, b) in enumerate(zip(expected_xml, body)) if a != b),
                    min(len(expected_xml), len(body)),
                )
                divergence = (
                    f"XML bytes differ (len {len(expected_xml)} != {len(body)}, "
                    f"first difference at offset {offset})"
                )
            mismatched.append(
                {
                    "uri": f"{SERVICE_ROOT}/$metadata",
                    "status": status,
                    "divergence": divergence,
                }
            )

    checked += 1
    odata_uri = f"{SERVICE_ROOT}/odata"
    odata_file = service_root / "odata" / "index.json"
    status, body = _http_get(base, odata_uri)
    if odata_file.is_file():
        served, error = _load_json_bytes(body)
        expected_odata = json.loads(odata_file.read_text(encoding="utf-8"))
        if status == 200 and error is None and served == expected_odata:
            matched += 1
        else:
            mismatched.append(
                {
                    "uri": odata_uri,
                    "status": status,
                    "divergence": error
                    or _first_divergence(expected_odata, served)
                    or f"expected 200 with the OData service document, got {status}",
                }
            )
    elif status == 404:
        matched += 1
    else:
        mismatched.append(
            {
                "uri": odata_uri,
                "status": status,
                "divergence": "expected 404: the profile ships no OData "
                "service document",
            }
        )

    if root_file is not None:
        checked += 1
        status, body = _http_get(base, SERVICE_ROOT + "/")
        served, error = _load_json_bytes(body)
        expected = json.loads(root_file.read_text(encoding="utf-8"))
        if status == 200 and error is None and served == expected:
            matched += 1
        else:
            mismatched.append(
                {
                    "uri": SERVICE_ROOT + "/",
                    "status": status,
                    "divergence": error
                    or _first_divergence(expected, served)
                    or f"expected the service root (trailing slash), got {status}",
                }
            )

    return checked, matched


def _check_profile(
    name: str,
    profile_dir: Path,
    serve_dir: Path,
    max_detail: int,
) -> dict[str, Any]:
    """Check one profile's declared surface against a freshly spawned mock.

    :param name: the profile name (report key).
    :param profile_dir: the profile root inside the bundle extraction (the
        source of declared URIs and expected documents).
    :param serve_dir: the directory the mock serves (normally ``profile_dir``;
        tests point it at a tampered copy).
    :param max_detail: bound on mismatch/absent detail entries in the report.
    :return: the per-profile report dictionary.
    """
    declared = _declared_uris(profile_dir)
    service_root = _service_root_dir(profile_dir)
    files = _file_uris(service_root)
    mismatched: list[dict[str, Any]] = []
    absent: list[str] = []
    matched = 0
    checked = 0
    special = {"/redfish", f"{SERVICE_ROOT}/$metadata", f"{SERVICE_ROOT}/odata"}

    with _mock_server(serve_dir) as base:
        special_checked, special_matched = _check_special_uris(
            base, profile_dir, service_root, files.get(SERVICE_ROOT), mismatched
        )
        checked += special_checked
        matched += special_matched

        for uri in sorted(declared - special):
            checked += 1
            status, body = _http_get(base, uri)
            backing = files.get(uri)
            if backing is None:
                verbatim = _direct_file(service_root, uri)
                if verbatim is not None:
                    if status == 200 and body == verbatim.read_bytes():
                        matched += 1
                    else:
                        mismatched.append(
                            {
                                "uri": uri,
                                "status": status,
                                "divergence": "expected 200 with the verbatim "
                                f"bundle file {verbatim.name}",
                            }
                        )
                elif status == 404:
                    absent.append(uri)
                else:
                    reason = "expected 404 (no file in the bundle)"
                    if status == 200:
                        reason = "mock served data for a URI with no bundle file"
                    mismatched.append(
                        {"uri": uri, "status": status, "divergence": reason}
                    )
                continue
            try:
                expected = json.loads(backing.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                mismatched.append(
                    {
                        "uri": uri,
                        "status": status,
                        "divergence": f"bundle file is not valid JSON: {exc}",
                    }
                )
                continue
            served, error = _load_json_bytes(body)
            if status != 200 or error is not None:
                mismatched.append(
                    {
                        "uri": uri,
                        "status": status,
                        "divergence": error or "expected 200 with the bundle document",
                    }
                )
                continue
            divergence = _first_divergence(expected, served)
            if divergence is None:
                matched += 1
            else:
                mismatched.append(
                    {"uri": uri, "status": status, "divergence": divergence}
                )

    orphans = sorted(set(files) - declared)
    _diag(
        f"profile {name}: declared={len(declared)} files={len(files)} "
        f"checked={checked} matched={matched} mismatched={len(mismatched)} "
        f"absent_declared={len(absent)} orphans={len(orphans)}"
    )
    return {
        "profile": name,
        "declared_uris": len(declared),
        "files_in_zip": len(files),
        "checked": checked,
        "matched": matched,
        "mismatched_total": len(mismatched),
        "mismatched": mismatched[:max_detail],
        "absent_declared": {"count": len(absent), "first": absent[:max_detail]},
        "coverage": {"files_never_referenced": orphans},
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    :param argv: argument vector; None reads ``sys.argv``.
    :return: the parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Check the mock server's --mockup-dir mode against the DSP2043 "
            "bundle: every declared @odata.id must be served 1:1 from its "
            "index.json, and URIs without a file must 404. JSON report on "
            "stdout; exit 0 clean, 1 on any mismatch, 2 on a usage or "
            "environment error."
        )
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=DEFAULT_BUNDLE,
        help="DSP2043 mockups zip (default: the spec/ tree's pinned bundle).",
    )
    parser.add_argument(
        "--profile",
        default="all",
        help="Profile directory name inside the bundle, or 'all' (default).",
    )
    parser.add_argument(
        "--serve-dir",
        type=Path,
        default=None,
        help="Serve this directory instead of the bundle's profile root "
        "(test seam; requires a single named --profile).",
    )
    parser.add_argument(
        "--max-detail",
        type=int,
        default=25,
        help="Bound on mismatch/absent detail entries per profile (default 25).",
    )
    args = parser.parse_args(argv)
    if args.serve_dir is not None and args.profile == "all":
        parser.error("--serve-dir requires a single named --profile")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the consistency check and print the JSON report.

    :param argv: argument vector; None reads ``sys.argv``.
    :return: exit code — 0 consistent, 1 on any mismatch, 2 on a usage or
        environment error.
    """
    args = parse_args(argv)
    try:
        bundle_root = _extract_bundle(args.bundle)
        available = _list_profiles(bundle_root)
        if not available:
            raise ToolError(
                f"no profile with a root index.json under {bundle_root}",
                "pass --bundle pointing at a DSP2043 mockups zip",
            )
        if args.profile == "all":
            selected = available
        elif args.profile in available:
            selected = [args.profile]
        else:
            raise ToolError(
                f"profile {args.profile!r} not in the bundle "
                f"(available: {', '.join(available)})",
                "pick one of the listed profiles or use --profile all",
            )
        _require_mockup_dir_support()

        profiles = []
        for name in selected:
            profile_dir = bundle_root / name
            serve_dir = args.serve_dir if args.serve_dir is not None else profile_dir
            profiles.append(
                _check_profile(name, profile_dir, serve_dir, args.max_detail)
            )
    except ToolError as exc:
        _diag(f"BLOCKER: {exc}")
        _diag(f"SAFE_NEXT_STEP: {exc.next_step}")
        return 2

    totals = {
        "profiles": len(profiles),
        "checked": sum(p["checked"] for p in profiles),
        "matched": sum(p["matched"] for p in profiles),
        "mismatched": sum(p["mismatched_total"] for p in profiles),
        "absent_declared": sum(p["absent_declared"]["count"] for p in profiles),
    }
    report = {"bundle": str(args.bundle), "profiles": profiles, "totals": totals}
    print(json.dumps(report, indent=2, sort_keys=False))
    return 1 if totals["mismatched"] else 0


if __name__ == "__main__":
    sys.exit(main())
