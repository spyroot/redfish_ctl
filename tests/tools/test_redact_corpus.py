"""Corpus redactor: sensitive fields are scrubbed, harmless data is preserved.

Guards the mechanical sanitize step contributors run before committing a crawled
Redfish tree (see docs/fixture-capture.md, Path A). Covers key-based redaction,
IP/MAC scrubbing in nested structures, the no-source-IP edge, and the end-to-end
directory pass including the <ip> rename.
"""
import json

from tools.redact_corpus import (
    DEFAULT_PLACEHOLDER_IP,
    SENSITIVE_KEYS,
    Counts,
    process_tree,
    redact_obj,
)


def _redact(obj, source_ips=("10.20.30.40",), placeholder="203.0.113.10"):
    return redact_obj(obj, list(source_ips), placeholder, SENSITIVE_KEYS, Counts())


def test_sensitive_keys_are_replaced():
    """Serial, service tag, UUID, MAC, and hostname values are overwritten."""
    src = {
        "SerialNumber": "CN7016xxxx",
        "ServiceTag": "ABC1234",
        "UUID": "4c4c4544-1234-5678-9abc-def012345678",
        "MACAddress": "aa:bb:cc:dd:ee:ff",
        "HostName": "lab-bmc-07",
    }
    out = _redact(src)
    assert out["SerialNumber"] == SENSITIVE_KEYS["serialnumber"]
    assert out["ServiceTag"] == SENSITIVE_KEYS["servicetag"]
    assert out["UUID"] == "00000000-0000-0000-0000-000000000000"
    assert out["MACAddress"] == "00:00:00:00:00:00"
    assert out["HostName"] == "redfish-host"


def test_key_match_is_case_insensitive():
    """`macaddress`/`MacAddress`/`MACADDRESS` all redact."""
    for key in ("macaddress", "MacAddress", "MACADDRESS"):
        assert _redact({key: "aa:bb:cc:dd:ee:ff"})[key] == "00:00:00:00:00:00"


def test_non_sensitive_fields_are_preserved():
    """Ordinary Redfish data is left untouched."""
    src = {"PowerState": "On", "Id": "System_0", "MemoryGiB": 512}
    assert _redact(src) == src


def test_source_ip_replaced_in_nested_values():
    """The management IP is replaced wherever it appears, including list-of-dicts."""
    src = {"IPv4Addresses": [{"Address": "10.20.30.40", "AddressOrigin": "Static"}]}
    out = _redact(src)
    assert out["IPv4Addresses"][0]["Address"] == "203.0.113.10"
    assert out["IPv4Addresses"][0]["AddressOrigin"] == "Static"


def test_mac_scrubbed_even_in_unexpected_field():
    """A MAC-shaped string is scrubbed regardless of its key (safety net)."""
    out = _redact({"Description": "uplink to aa:bb:cc:11:22:33"})
    assert "aa:bb:cc:11:22:33" not in out["Description"]
    assert "00:00:00:00:00:00" in out["Description"]


def test_no_source_ip_still_redacts_keys():
    """With no IP to match, key/MAC redaction still runs and IPs are just left as-is."""
    counts = Counts()
    out = redact_obj({"SerialNumber": "X", "Note": "10.0.0.1"}, [], "203.0.113.10",
                     SENSITIVE_KEYS, counts)
    assert out["SerialNumber"] == SENSITIVE_KEYS["serialnumber"]
    assert out["Note"] == "10.0.0.1"  # untouched — no source IP given
    assert counts.ip_replacements == 0


def test_process_tree_writes_placeholder_dir_and_scrubs(tmp_path):
    """End-to-end: a crawled <ip> tree redacts into <placeholder-ip>/ with clean JSON."""
    src_dir = tmp_path / "10.20.30.40"
    src_dir.mkdir()
    (src_dir / "_redfish_v1_Managers_BMC_0.json").write_text(json.dumps({
        "SerialNumber": "CN7016xxxx",
        "MACAddress": "aa:bb:cc:dd:ee:ff",
        "IPv4Addresses": [{"Address": "10.20.30.40"}],
        "PowerState": "On",
    }))
    out_root = tmp_path / "out"
    counts = process_tree(src_dir, out_root, ["10.20.30.40"], DEFAULT_PLACEHOLDER_IP)

    written = out_root / DEFAULT_PLACEHOLDER_IP / "_redfish_v1_Managers_BMC_0.json"
    assert written.is_file()
    data = json.loads(written.read_text())
    assert data["SerialNumber"] == SENSITIVE_KEYS["serialnumber"]
    assert data["MACAddress"] == "00:00:00:00:00:00"
    assert data["IPv4Addresses"][0]["Address"] == "203.0.113.10"
    assert data["PowerState"] == "On"
    assert counts.files == 1
    # The original management IP must not survive anywhere in the output.
    assert "10.20.30.40" not in written.read_text()
