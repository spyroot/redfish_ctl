"""Characterize replay of captured non-2xx corpus responses."""

from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from vendor_corpus import corpus_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_MODULE = REPO_ROOT / "k8s" / "sandbox" / "mock_bmc_server.py"


def _load_server_module() -> Any:
    spec = importlib.util.spec_from_file_location("mock_bmc_server", SERVER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _http(base: str, path: str) -> tuple[int, Any]:
    request = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, json.loads(raw) if raw else None


FULL_CORPORA = (
    (
        "Dell XR8620t",
        REPO_ROOT / "full_corpus" / "dell_xr8620t_full_corpus.tar.gz",
        "10.252.252.209",
    ),
    (
        "HPE DL360",
        REPO_ROOT / "full_corpus" / "hpe_dl360_full_corpus.tar.gz",
        "10.43.3.209",
    ),
    (
        "Supermicro X10",
        REPO_ROOT / "full_corpus" / "supermicro_x10_full_corpus.tar.gz",
        "192.168.254.119",
    ),
    (
        "Supermicro GB300",
        REPO_ROOT / "full_corpus" / "supermicro_gb300_full_corpus.tar.gz",
        "172.25.230.37",
    ),
)


CAPTURED_ERROR_CORPUS_VALUES = (
    (
        "Supermicro X10",
        REPO_ROOT / "full_corpus" / "supermicro_x10_full_corpus.tar.gz",
        "192.168.254.119",
    ),
)


def _captured_error_cases(tarball: Path, leaf: str) -> list[tuple[str, int]]:
    corpus = corpus_dir(tarball, leaf)
    api_map = np.load(corpus / "rest_api_map.npy", allow_pickle=True).item()
    status_map = api_map.get("http_status_mapping", {}) or {}
    error_map = api_map.get("error_file_mapping", {}) or {}
    return [
        (url, int(status))
        for url, status in status_map.items()
        if (int(status) < 200 or int(status) >= 300) and url in error_map
    ]


def test_captured_error_case_list_covers_committed_full_corpora() -> None:
    """Keep the replay matrix aligned with committed corpus map evidence."""
    found = {
        vendor_model
        for vendor_model, tarball, leaf in FULL_CORPORA
        if _captured_error_cases(tarball, leaf)
    }
    expected = {
        vendor_model
        for vendor_model, _tarball, _leaf in CAPTURED_ERROR_CORPUS_VALUES
    }
    assert found == expected


@pytest.mark.parametrize(
    ("vendor_model", "tarball", "leaf"),
    [
        pytest.param(vendor_model, tarball, leaf, id="supermicro-x10")
        for vendor_model, tarball, leaf in CAPTURED_ERROR_CORPUS_VALUES
    ],
)
def test_captured_non_2xx_responses_replay_status_and_body(
    vendor_model: str,
    tarball: Path,
    leaf: str,
) -> None:
    """Each captured error response replays its real status with an error body."""
    corpus = corpus_dir(tarball, leaf)
    cases = _captured_error_cases(tarball, leaf)
    assert cases, f"{vendor_model}: expected at least one captured error response"

    module = _load_server_module()
    with module.run_server("127.0.0.1", 0, corpus) as server:
        base = "http://{}:{}".format(*server.server_address)
        for path, expected_status in cases:
            status, body = _http(base, path)
            assert status == expected_status, f"{vendor_model}: {path}"
            assert isinstance(body, dict), f"{vendor_model}: {path}"
            assert body.get("error"), f"{vendor_model}: {path}"
