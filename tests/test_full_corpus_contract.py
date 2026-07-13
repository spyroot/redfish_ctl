"""Rock-solid contract tests for the full training corpus + rest_api_map.npy.

Pins the ``tools/pack_full_corpus`` producer and the ``rest_api_map.npy`` contract
so a full corpus can never silently drop resources, mismatch its map, downgrade a
writable method to read-only, or over-redact. See docs/full-corpus-contract.md.

The map contract (per the handoff spec):
- loads with ``np.load(path, allow_pickle=True).item()``;
- has top-level ``url_file_mapping`` and ``allowed_methods_mapping``;
- every mapped file exists; every resource JSON is mapped; every methods-url is in url_file_mapping;
- writable methods (POST/PATCH/PUT/DELETE) are preserved, never collapsed to GET/HEAD.
"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import numpy as np
import pytest

from tools import pack_full_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_host_dir(root: Path, resources: dict, methods: dict) -> Path:
    """Create a synthetic discovery host dir + a same-shape rest_api_map.npy."""
    host = root / "10.0.0.9"
    host.mkdir()
    url_file = {}
    for url, (fname, body) in resources.items():
        (host / fname).write_text(json.dumps(body, indent=4))
        url_file[url] = fname
    np.save(host / "rest_api_map.npy",
            {"url_file_mapping": url_file, "allowed_methods_mapping": methods})
    return host


@pytest.fixture
def good_host(tmp_path):
    """A minimal but valid full-corpus host dir (a read, a PATCH, and a POST target)."""
    resources = {
        "/redfish/v1/": ("_redfish_v1.json", {"RedfishVersion": "1.17.0", "@odata.id": "/redfish/v1/"}),
        "/redfish/v1/Systems/1/Bios/Settings": (
            "_redfish_v1_Systems_1_Bios_Settings.json",
            {"@odata.id": "/redfish/v1/Systems/1/Bios/Settings", "Attributes": {"AdminName": "op"}}),
        "/redfish/v1/Managers/1/Accounts": (
            "_redfish_v1_Managers_1_Accounts.json",
            {"@odata.id": "/redfish/v1/Managers/1/Accounts",
             "Members": [{"UserName": "root", "Password": "s3cr3t-hash", "SerialNumber": "ABC123"}]}),
    }
    methods = {
        "/redfish/v1/": ["GET", "HEAD"],
        "/redfish/v1/Systems/1/Bios/Settings": ["GET", "HEAD", "PATCH"],
        "/redfish/v1/Managers/1/Accounts": ["GET", "HEAD", "POST"],
    }
    return _write_host_dir(tmp_path, resources, methods)


def test_map_loads_and_has_required_keys(good_host):
    """rest_api_map.npy loads with allow_pickle and carries both required mappings."""
    api = pack_full_corpus.load_api_map(good_host)
    assert set(api) >= {"url_file_mapping", "allowed_methods_mapping"}
    api2 = np.load(good_host / "rest_api_map.npy", allow_pickle=True).item()
    assert api2 == api


def test_valid_corpus_passes_gate(good_host):
    """A complete, consistent host dir produces zero validation problems."""
    api = pack_full_corpus.load_api_map(good_host)
    files = sorted(good_host.glob("*.json"))
    assert pack_full_corpus.validate(good_host, api, files) == []


def test_writable_methods_preserved_in_manifest(good_host):
    """PATCH/POST are counted, never downgraded to GET/HEAD."""
    api = pack_full_corpus.load_api_map(good_host)
    files = sorted(good_host.glob("*.json"))
    m = pack_full_corpus.build_manifest(good_host, api, "acme", "x", files, "x")
    assert m["method_counts"]["PATCH"] == 1
    assert m["method_counts"]["POST"] == 1
    assert m["artifact_type"] == "full_training"
    assert m["json_file_count"] == m["url_file_mapping_count"] == len(files)


def test_gate_fails_on_unmapped_resource(good_host):
    """A resource JSON with no url_file_mapping entry is a fail-closed violation."""
    (good_host / "_redfish_v1_Chassis_1.json").write_text('{"@odata.id":"/redfish/v1/Chassis/1"}')
    api = pack_full_corpus.load_api_map(good_host)
    files = sorted(good_host.glob("*.json"))
    problems = pack_full_corpus.validate(good_host, api, files)
    assert any("not in url_file_mapping" in p for p in problems)


def test_gate_fails_on_missing_mapped_file(good_host):
    """A url_file_mapping pointing at a nonexistent file fails the gate."""
    api = pack_full_corpus.load_api_map(good_host)
    api["url_file_mapping"]["/redfish/v1/Ghost"] = "_redfish_v1_Ghost.json"
    np.save(good_host / "rest_api_map.npy", api)
    api = pack_full_corpus.load_api_map(good_host)
    files = sorted(good_host.glob("*.json"))
    problems = pack_full_corpus.validate(good_host, api, files)
    assert any("mapped file missing" in p for p in problems)


def test_redaction_only_touches_credentials_and_username(good_host):
    """Redaction scrubs Password + UserName; leaves SerialNumber (and all else) original."""
    body = json.loads((good_host / "_redfish_v1_Managers_1_Accounts.json").read_text())
    cleaned = pack_full_corpus._redact_credentials(body)
    member = cleaned["Members"][0]
    assert member["Password"] == "REDACTED"
    assert member["UserName"] == "REDACTED"
    assert member["SerialNumber"] == "ABC123"  # NOT redacted — full corpus keeps identifiers


def test_full_pack_roundtrips_and_revalidates(good_host, tmp_path):
    """Packing produces a tarball that unpacks to one host dir with map+manifest and re-validates."""
    out = tmp_path / "acme_x_full_corpus.tar.gz"
    rc = pack_full_corpus.pack(good_host, out, "acme", "x", redact=True)
    assert rc == 0 and out.exists()
    unpack = tmp_path / "unpack"
    with tarfile.open(out) as tar:
        tar.extractall(unpack)
    root = unpack / good_host.name
    assert (root / "rest_api_map.npy").exists()
    manifest = json.loads((root / "corpus_manifest.json").read_text())
    assert manifest["artifact_type"] == "full_training"
    assert manifest["redaction_status"] == "credentials_username_redacted"
    # re-validate the unpacked corpus
    api = pack_full_corpus.load_api_map(root)
    files = sorted(p for p in root.glob("*.json") if p.name != "corpus_manifest.json")
    assert pack_full_corpus.validate(root, api, files) == []
    # credential redaction survived the pack
    acct = json.loads((root / "_redfish_v1_Managers_1_Accounts.json").read_text())
    assert acct["Members"][0]["Password"] == "REDACTED"
    assert acct["Members"][0]["SerialNumber"] == "ABC123"
