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
    assert any("not mapped" in p for p in problems)


def test_gate_fails_on_missing_mapped_file(good_host):
    """A url_file_mapping pointing at a nonexistent file fails the gate."""
    api = pack_full_corpus.load_api_map(good_host)
    api["url_file_mapping"]["/redfish/v1/Ghost"] = "_redfish_v1_Ghost.json"
    np.save(good_host / "rest_api_map.npy", api)
    api = pack_full_corpus.load_api_map(good_host)
    files = sorted(good_host.glob("*.json"))
    problems = pack_full_corpus.validate(good_host, api, files)
    assert any("mapped file missing" in p for p in problems)


def test_gate_fails_on_missing_serviceroot(good_host):
    """A corpus without the ServiceRoot (_redfish_v1.json) fails the gate."""
    (good_host / "_redfish_v1.json").unlink()
    api = pack_full_corpus.load_api_map(good_host)
    del api["url_file_mapping"]["/redfish/v1/"]
    np.save(good_host / "rest_api_map.npy", api)
    api = pack_full_corpus.load_api_map(good_host)
    files = sorted(good_host.glob("*.json"))
    problems = pack_full_corpus.validate(good_host, api, files)
    assert any("ServiceRoot" in p for p in problems)


def test_redaction_only_touches_credentials_and_username(good_host):
    """Redaction scrubs Password + UserName; leaves SerialNumber (and all else) original."""
    body = json.loads((good_host / "_redfish_v1_Managers_1_Accounts.json").read_text())
    cleaned = pack_full_corpus._redact_credentials(body)
    member = cleaned["Members"][0]
    assert member["Password"] == "REDACTED"
    assert member["UserName"] == "REDACTED"
    assert member["SerialNumber"] == "ABC123"  # NOT redacted — full corpus keeps identifiers


def test_snmpv3_key_family_is_redacted():
    """Regression: EVERY SNMPv3 localized key variant is scrubbed, not just the ones
    spelled out in the set. A Dell ``SHA1v3Key`` once leaked because the exact-match
    set held ``shav3key`` (no such Dell key) while the real ``SHA1v3Key`` lowercases
    to ``sha1v3key``; its siblings ``MD5v3Key``/``SHA256Password`` were scrubbed, so
    the corpus shipped 3 live SNMPv3 auth keys. The ``*v3key`` suffix pattern closes
    this whole class."""
    body = {
        "Attributes": {
            "Users.2.SHA1v3Key": "0123456789abcdef0123456789abcdef01234567",
            "Users.3.SHA256v3Key": "deadbeef" * 8,
            "Users.4.MD5v3Key": "cafebabecafebabecafebabecafebabe",
            "Users.5.ShaV3Key": "feedface" * 5,
        }
    }
    cleaned = pack_full_corpus._redact_credentials(body)["Attributes"]
    for k in body["Attributes"]:
        assert cleaned[k] == "REDACTED", f"{k} survived redaction"


def test_community_and_extra_secret_key_classes_redacted():
    """Community strings and the newly-covered key classes are scrubbed. ``*community``
    catches RO/RW/Agent community; ``EncryptionKey`` (IPMI) and ``BindPassword`` (LDAP)
    are added exact keys — all secret material a full corpus must not ship."""
    body = {
        "ROCommunity": "not-public-secret",
        "RWCommunity": "not-private-secret",
        "SNMPAlert.1.SNMPv3Community": "custom-community",
        "IPMILan.1.EncryptionKey": "1122334455667788990011223344556677889900",
        "LDAP.1.BindPassword": "s3cr3t-bind",
    }
    cleaned = pack_full_corpus._redact_credentials(body)
    for k in body:
        assert cleaned[k] == "REDACTED", f"{k} survived redaction"


def test_non_secret_config_keys_are_not_over_redacted():
    """The redactor must NOT collapse non-secret config that merely resembles a secret
    key. Password-*policy* fields, protocol names, and numeric values stay ORIGINAL so
    the corpus keeps its device configuration faithful."""
    body = {
        "PasswordExpiration": "90",          # policy value, last segment != 'password'
        "MinimumPasswordLength": 8,           # non-string, untouched
        "SNMPProtocol": "SNMPv3",             # protocol name, not a key
        "CommunityNameEnabled": "true",       # boolean-ish flag, last segment not matched
        "SerialNumber": "CN7016327K0033",     # identifier, kept by policy
    }
    cleaned = pack_full_corpus._redact_credentials(body)
    assert cleaned == body, "a non-secret config value was over-redacted"


def test_dell_dellattributes_shape_redaction():
    """On the real Dell DellAttributes shape (dotted composite keys under Attributes),
    only the SNMPv3 key + account username are scrubbed; the alert destination and
    other attributes stay original."""
    body = {
        "@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes/iDRAC.Embedded.1",
        "Attributes": {
            "Users.2.SHA1v3Key": "0123456789abcdef0123456789abcdef01234567",
            "Users.2.UserName": "root",
            "SNMPAlert.1.Destination": "10.0.0.5",
            "Time.1.Timezone": "US/Central",
        },
    }
    attrs = pack_full_corpus._redact_credentials(body)["Attributes"]
    assert attrs["Users.2.SHA1v3Key"] == "REDACTED"
    assert attrs["Users.2.UserName"] == "REDACTED"
    assert attrs["SNMPAlert.1.Destination"] == "10.0.0.5"   # not a secret — kept
    assert attrs["Time.1.Timezone"] == "US/Central"


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
    assert manifest["artifact_checksum"].startswith("sha256:")
    assert len(manifest["artifact_checksum"]) == len("sha256:") + 64
    assert manifest["artifact_checksum"] == pack_full_corpus.artifact_payload_checksum(root)
    # re-validate the unpacked corpus
    api = pack_full_corpus.load_api_map(root)
    files = sorted(p for p in root.glob("*.json") if p.name != "corpus_manifest.json")
    assert pack_full_corpus.validate(root, api, files) == []
    # credential redaction survived the pack
    acct = json.loads((root / "_redfish_v1_Managers_1_Accounts.json").read_text())
    assert acct["Members"][0]["Password"] == "REDACTED"
    assert acct["Members"][0]["SerialNumber"] == "ABC123"


def _write_capture_host(root):
    """A host dir shaped like the lossless capture: ServiceRoot, a 2xx resource,
    and a captured non-2xx response (``.error.json``) with the additive
    ``http_status_mapping`` + ``error_file_mapping`` the producer now writes."""
    host = root / "10.0.0.9"
    host.mkdir()
    (host / "_redfish_v1.json").write_text(json.dumps(
        {"@odata.id": "/redfish/v1/", "RedfishVersion": "1.6.0"}, indent=4))
    (host / "_redfish_v1_Systems_1.json").write_text(json.dumps(
        {"@odata.id": "/redfish/v1/Systems/1"}, indent=4))
    # a real captured 404 Redfish error envelope, saved under a .error.json name
    (host / "_redfish_v1_Chassis_1_PCIeDevices_178.error.json").write_text(json.dumps(
        {"error": {"code": "Base.1.17.ResourceMissingAtURI",
                   "@Message.ExtendedInfo": [{"MessageId": "Base.1.17.ResourceMissingAtURI"}]}}, indent=4))
    api = {
        "url_file_mapping": {
            "/redfish/v1/": "_redfish_v1.json",
            "/redfish/v1/Systems/1": "_redfish_v1_Systems_1.json",
        },
        "allowed_methods_mapping": {
            "/redfish/v1/": ["GET", "HEAD"],
            "/redfish/v1/Systems/1": ["GET", "HEAD", "PATCH"],
            "/redfish/v1/Chassis/1/PCIeDevices/178": ["GET", "HEAD"],
        },
        "http_status_mapping": {
            "/redfish/v1/": 200,
            "/redfish/v1/Systems/1": 200,
            "/redfish/v1/Chassis/1/PCIeDevices/178": 404,
        },
        "error_file_mapping": {
            "/redfish/v1/Chassis/1/PCIeDevices/178":
                "_redfish_v1_Chassis_1_PCIeDevices_178.error.json",
        },
    }
    np.save(host / "rest_api_map.npy", api)
    (host / "rest_api_map.status.json").write_text(json.dumps(
        {
            "http_status_mapping": api["http_status_mapping"],
            "error_file_mapping": api["error_file_mapping"],
        },
        indent=2,
    ))
    return host


def test_error_capture_corpus_validates(tmp_path):
    """A corpus that captured a non-2xx response is valid: the error body is a
    mapped resource (via error_file_mapping), not an orphan, and json_file_count
    reconciles url_file_mapping + error_file_mapping."""
    host = _write_capture_host(tmp_path)
    api = pack_full_corpus.load_api_map(host)
    files = sorted(host.glob("*.json"))
    assert pack_full_corpus.validate(host, api, files) == []


def test_error_capture_manifest_counts(tmp_path):
    """The manifest surfaces the captured status distribution + error count so a
    consumer can see the corpus carries error ground truth."""
    host = _write_capture_host(tmp_path)
    api = pack_full_corpus.load_api_map(host)
    files = sorted(host.glob("*.json"))
    m = pack_full_corpus.build_manifest(host, api, "acme", "x", files, "x")
    assert m["url_file_mapping_count"] == 2
    assert m["error_file_mapping_count"] == 1
    assert m["json_file_count"] == 3
    assert m["http_status_counts"].get("2xx") == 2
    assert m["http_status_counts"].get("4xx") == 1


def test_error_file_orphan_still_fails(tmp_path):
    """An .error.json on disk that is NOT in error_file_mapping is still an orphan
    (fail closed) — capture must map every file it writes."""
    host = _write_capture_host(tmp_path)
    (host / "_redfish_v1_Ghost.error.json").write_text('{"error":{}}')
    api = pack_full_corpus.load_api_map(host)
    files = sorted(host.glob("*.json"))
    problems = pack_full_corpus.validate(host, api, files)
    assert any("not mapped" in p for p in problems)


def test_url_in_both_url_and_error_mapping_fails(tmp_path):
    """A URL must be EITHER a 2xx (url_file_mapping) OR an error
    (error_file_mapping), never both — an overlap is a producer bug, fail closed."""
    host = _write_capture_host(tmp_path)
    api = pack_full_corpus.load_api_map(host)
    api["error_file_mapping"]["/redfish/v1/Systems/1"] = "_redfish_v1_Systems_1.json"
    np.save(host / "rest_api_map.npy", api)
    api = pack_full_corpus.load_api_map(host)
    files = sorted(host.glob("*.json"))
    problems = pack_full_corpus.validate(host, api, files)
    assert any("BOTH" in p for p in problems)


def test_error_capture_full_pack_roundtrips(tmp_path):
    """Packing a capture corpus round-trips: the error body + both additive
    mappings survive, and the unpacked corpus re-validates."""
    host = _write_capture_host(tmp_path)
    out = tmp_path / "acme_x_full_corpus.tar.gz"
    rc = pack_full_corpus.pack(host, out, "acme", "x", redact=True)
    assert rc == 0 and out.exists()
    unpack = tmp_path / "unpack"
    with tarfile.open(out) as tar:
        tar.extractall(unpack)
    root = unpack / host.name
    assert (root / "_redfish_v1_Chassis_1_PCIeDevices_178.error.json").exists()
    sidecar = root / "rest_api_map.status.json"
    assert sidecar.exists()
    api = pack_full_corpus.load_api_map(root)
    sidecar_data = json.loads(sidecar.read_text())
    assert sidecar_data == {
        "http_status_mapping": api["http_status_mapping"],
        "error_file_mapping": api["error_file_mapping"],
    }
    assert api["http_status_mapping"]["/redfish/v1/Chassis/1/PCIeDevices/178"] == 404
    assert "/redfish/v1/Chassis/1/PCIeDevices/178" in api["error_file_mapping"]
    files = sorted(p for p in root.glob("*.json") if p.name != "corpus_manifest.json")
    assert pack_full_corpus.validate(root, api, files) == []


# --------------------------------------------------------------------------- #
# Sanity gate over the REAL committed full_corpus/*.tar.gz maps. The tests above
# pin the contract on synthetic host dirs; these validate the ACTUAL shipped
# rest_api_map.npy files, so a corrupt or incomplete map fails the build. The
# maps legitimately vary (2 keys or 4; filename or capture-host absolute path).
# --------------------------------------------------------------------------- #

_FULL_CORPUS_TARBALLS = sorted((REPO_ROOT / "full_corpus").glob("*_full_corpus.tar.gz"))
_KNOWN_NPY_KEYS = {"url_file_mapping", "allowed_methods_mapping",
                   "http_status_mapping", "error_file_mapping"}
_REQUIRED_NPY_KEYS = {"url_file_mapping", "allowed_methods_mapping"}
_EXPECTED_TARBALLS = {
    "dell_xr8620t_full_corpus.tar.gz", "hpe_dl360_full_corpus.tar.gz",
    "supermicro_gb300_full_corpus.tar.gz", "supermicro_x10_full_corpus.tar.gz",
}


def _is_lfs_pointer(path: Path) -> bool:
    """Whether ``path`` is an unfetched Git-LFS pointer instead of the real blob.

    :param path: the file to inspect.
    :return: True when its first bytes carry the LFS pointer signature.
    """
    with open(path, "rb") as handle:
        return handle.read(40).startswith(b"version https://git-lfs")


def test_full_corpus_tarballs_present() -> None:
    """The four vendor full_corpus tarballs are committed (LFS pointers count).

    Guards against a silently-empty gate: if the artifacts vanish, the
    parametrized soundness test would collect zero cases and pass vacuously.

    :return: None.
    """
    names = {t.name for t in _FULL_CORPUS_TARBALLS}
    assert _EXPECTED_TARBALLS <= names, f"missing full_corpus tarballs: {_EXPECTED_TARBALLS - names}"


@pytest.mark.parametrize(
    "tarball", _FULL_CORPUS_TARBALLS, ids=[t.name for t in _FULL_CORPUS_TARBALLS]
)
def test_committed_full_corpus_npy_is_sound(tarball: Path, tmp_path: Path) -> None:
    """Validate a shipped full_corpus rest_api_map.npy against the map contract.

    Runs on the ACTUAL committed artifact (not synthetic data): the npy loads
    with ``allow_pickle``, carries the two required maps and only known keys,
    ``url_file_mapping`` is non-empty, every mapped value resolves to a JSON file
    in the archive by basename (older captures store absolute capture-host
    paths), and ``allowed_methods_mapping`` is non-empty. Skips a tarball that is
    still an unfetched LFS pointer.

    :param tarball: the full_corpus/*.tar.gz to validate.
    :param tmp_path: pytest-provided temp dir the tarball extracts into.
    :return: None.
    """
    if _is_lfs_pointer(tarball):
        pytest.skip(f"{tarball.name} is an unfetched LFS pointer — run `git lfs pull`")
    with tarfile.open(tarball) as archive:
        archive.extractall(tmp_path)  # noqa: S202 - our own trusted committed corpus
    maps = list(tmp_path.rglob("rest_api_map.npy"))
    assert len(maps) == 1, f"{tarball.name}: expected one rest_api_map.npy, found {len(maps)}"
    root = maps[0].parent
    api = np.load(maps[0], allow_pickle=True).item()
    keys = set(api)
    assert _REQUIRED_NPY_KEYS <= keys, f"{tarball.name}: missing required keys {_REQUIRED_NPY_KEYS - keys}"
    assert keys <= _KNOWN_NPY_KEYS, f"{tarball.name}: unexpected keys {keys - _KNOWN_NPY_KEYS}"
    if {"http_status_mapping", "error_file_mapping"} <= keys:
        sidecar = root / "rest_api_map.status.json"
        assert sidecar.exists(), f"{tarball.name}: missing rest_api_map.status.json"
        assert json.loads(sidecar.read_text()) == {
            "http_status_mapping": {
                path: int(status)
                for path, status in (api.get("http_status_mapping", {}) or {}).items()
            },
            "error_file_mapping": {
                path: str(filename)
                for path, filename in (api.get("error_file_mapping", {}) or {}).items()
            },
        }
    url_map = api["url_file_mapping"]
    assert url_map, f"{tarball.name}: url_file_mapping is empty"
    unresolved = [u for u, f in url_map.items() if not (root / Path(str(f)).name).exists()]
    assert not unresolved, f"{tarball.name}: {len(unresolved)} unresolved url_file_mapping entries, e.g. {unresolved[:3]}"
    assert api["allowed_methods_mapping"], f"{tarball.name}: allowed_methods_mapping is empty"
