"""Contract tests for the canonical Redfish corpus manifest."""
from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from pathlib import Path

import numpy as np
import pytest

from redfish_ctl import corpora

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "corpora" / "manifest.v1.json"
REQUIRED_KEYS = {
    "id",
    "kind",
    "vendor",
    "model",
    "capture_id",
    "platform_label",
    "source",
    "archive",
    "archive_sha256",
    "archive_bytes",
    "root",
    "redfish_version",
    "json_count",
    "archive_json_count",
    "status",
    "contains_rest_api_map",
    "contains_logs",
    "contains_log_entries",
    "contains_events",
    "contains_schemas",
    "contains_registries",
    "sanitized",
    "redaction_version",
    "license_note",
    "consumers",
    "completeness",
    "notes",
}


def test_manifest_v1_separates_mock_and_dataset_artifacts():
    """The manifest is authoritative and never calls filtered mock tarballs datasets."""
    manifest = corpora.load_manifest()
    rows = manifest.corpora

    assert MANIFEST_PATH.exists()
    assert manifest.schema_version == 1
    assert {row.kind for row in rows} >= {"mock", "dataset"}
    assert {
        (row.kind, row.vendor, row.model)
        for row in rows
    } >= {
        ("mock", "dell", "xr8620t"),
        ("mock", "hpe", "dl360"),
        ("mock", "supermicro", "x10sdv"),
        ("mock", "supermicro", "gb300"),
        ("mock", "nvidia", "gb300-node2"),
        ("dataset", "dell", "xr8620t"),
    }
    mock_dell = corpora.resolve("dell-xr8620t", kind="mock")
    dataset_dell = corpora.resolve("dell-xr8620t-dataset-2023-06-17", kind="dataset")
    assert mock_dell is not None and dataset_dell is not None
    assert mock_dell.json_count == 995
    assert dataset_dell.json_count == 2466
    assert "not asserted" in dataset_dell.completeness


@pytest.mark.parametrize("row", corpora.load_manifest().corpora, ids=lambda r: r.id)
def test_manifest_rows_have_v1_shape_and_repo_archives(row):
    """Each row has required metadata and a repo-local Git-LFS archive path."""
    raw = row.asdict()
    assert REQUIRED_KEYS <= set(raw)
    assert row.kind in {"mock", "dataset"}
    assert row.archive.startswith(f"corpora/{row.kind}/")
    assert row.archive.endswith(".tar.gz")
    assert row.archive_path.exists()
    assert row.archive_path.stat().st_size == row.archive_bytes
    assert corpora.sha256_file(row.archive_path) == row.archive_sha256
    assert row.json_count > 0
    assert row.archive_json_count >= row.json_count
    assert row.root
    assert row.sanitized is True
    assert row.status in {"active", "incomplete", "deprecated"}
    assert "redfish_ctl" in row.consumers


@pytest.mark.parametrize("row", corpora.load_manifest().corpora, ids=lambda r: r.id)
def test_json_count_and_archive_root_match(row):
    """json_count and source root match resource JSON inside each archive."""
    if corpora.is_lfs_pointer(row.archive_path):
        pytest.skip(f"{row.archive} is a bare LFS pointer; run `python tools/corpus.py pull`")
    with tarfile.open(row.archive_path) as tar:
        names = tar.getnames()
    assert row.root in {
        "/".join(name.split("/")[:len(Path(row.root).parts)])
        for name in names
        if name
    }
    assert sum(1 for _ in corpora.iter_json_files(row)) == row.json_count


def test_resolve_supports_id_and_vendor_model_per_artifact():
    """Consumers resolve mock and dataset captures without raw archive roots."""
    mock = corpora.resolve("dell-xr8620t", kind="mock")
    by_pair = corpora.resolve(vendor="dell", model="xr8620t", kind="mock")
    dataset = corpora.resolve(vendor="dell", model="xr8620t", kind="dataset")

    assert mock == by_pair
    assert mock.archive == "corpora/mock/dell_xr8620t.tar.gz"
    assert dataset.archive == "corpora/dataset/dell_xr8620t_2023-06-17.tar.gz"
    assert corpora.resolve("missing-model") is None


def test_materialize_mock_uses_stable_flat_leaf(tmp_path):
    """Mock materialization yields one isolated flat leaf for mock_bmc_server."""
    row = corpora.resolve("dell-xr8620t", kind="mock")
    if corpora.is_lfs_pointer(row.archive_path):
        pytest.skip(f"{row.archive} is a bare LFS pointer; run `python tools/corpus.py pull`")

    outputs = corpora.materialize(tmp_path, corpus_id=row.id, kind="mock")

    assert outputs == [tmp_path / "mock" / "dell_xr8620t"]
    assert not (outputs[0] / row.root).exists()
    assert (outputs[0] / "_redfish_v1.json").exists()
    assert len(list(outputs[0].glob("*.json"))) == row.json_count
    assert len(list(outputs[0].rglob("*.json"))) == row.json_count


def test_materialize_dataset_preserves_recursive_map_layout(tmp_path):
    """Dataset materialization keeps maps and nested resource JSON separate."""
    row = corpora.resolve("dell-xr8620t-dataset-2023-06-17", kind="dataset")
    if corpora.is_lfs_pointer(row.archive_path):
        pytest.skip(f"{row.archive} is a bare LFS pointer; run `python tools/corpus.py pull --kind dataset`")

    outputs = corpora.materialize(tmp_path, corpus_id=row.id, kind="dataset")
    root = outputs[0]
    portable = json.loads((root / "rest_api_map.v1.json").read_text())
    legacy = np.load(root / "rest_api_map.npy", allow_pickle=True).item()

    assert root == tmp_path / "dataset" / "dell_xr8620t_2023-06-17"
    assert (root / "json_responses" / "_redfish_v1.json").exists()
    assert portable == legacy
    assert set(portable) == {"url_file_mapping", "allowed_methods_mapping"}
    assert len(portable["url_file_mapping"]) == row.json_count
    assert all((root / path).is_file() for path in portable["url_file_mapping"].values())


def test_tools_corpus_legacy_extract_all_still_materializes_mock_leaf(tmp_path):
    """Existing `python tools/corpus.py extract-all --dest` behavior is preserved."""
    result = subprocess.run(
        [
            sys.executable,
            "tools/corpus.py",
            "extract-all",
            "--vendor",
            "hpe",
            "--dest",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "hpe/dl360" in result.stdout
    assert (tmp_path / "hpe_dl360" / "_redfish_v1.json").exists()


def test_tools_corpus_strict_verify_checks_selected_dataset():
    """Strict verification succeeds only after materialized LFS objects are present."""
    result = subprocess.run(
        [
            sys.executable,
            "tools/corpus.py",
            "verify",
            "--kind",
            "dataset",
            "--vendor",
            "dell",
            "--require-materialized",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "ok" in result.stdout


def test_package_cli_lists_corpora_without_bmc_credentials(monkeypatch):
    """The package CLI exposes `redfish_ctl corpus list` as a local command."""
    monkeypatch.delenv("REDFISH_IP", raising=False)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "redfish_ctl",
            "--json_only",
            "--nocolor",
            "corpus",
            "list",
            "--kind",
            "dataset",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    payload = json.loads(result.stdout)
    data = payload["data"]

    assert data["schema_version"] == 1
    assert any(row["id"] == "dell-xr8620t-dataset-2023-06-17" for row in data["corpora"])


def test_package_cli_files_lists_slug_relative_paths():
    """`corpus files` reports materialized paths, not raw capture roots."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "redfish_ctl",
            "--json_only",
            "--nocolor",
            "corpus",
            "files",
            "--id",
            "dell-xr8620t",
            "--kind",
            "mock",
            "--limit",
            "3",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    payload = json.loads(result.stdout)
    data = payload["data"]

    assert data["id"] == "dell-xr8620t"
    assert data["files"]
    assert all(path.startswith("mock/dell_xr8620t/") for path in data["files"])
    assert all("10.252.252.209" not in path for path in data["files"])
