"""Offline tests for the discovery output producer (the igc .npy contract).

`Discovery.save_url_file_mapping` writes `rest_api_map.npy` — the artifact the
igc project consumes via `np.load(..., allow_pickle=True).item()`. These tests
pin the file name and the two top-level keys so a refactor cannot silently break
that cross-repo contract. No iDRAC, no network.
"""
import numpy as np

from redfish_ctl.discovery.cmd_discovery import Discovery


def _new_discovery(tmp_path):
    """A Discovery with __init__ bypassed and just the maps save() reads."""
    disc = Discovery.__new__(Discovery)
    disc.json_response_dir = str(tmp_path)
    disc._discovered_url_file_mapping = {}
    disc._api_allowed_methods = {}
    disc._http_status = {}
    disc._error_file_mapping = {}
    return disc


def test_save_url_file_mapping_roundtrip(tmp_path):
    """save_url_file_mapping writes rest_api_map.npy that round-trips to its inputs,
    including the additive status + error-capture maps."""
    disc = _new_discovery(tmp_path)
    disc._discovered_url_file_mapping = {"/redfish/v1/A": str(tmp_path / "A.json")}
    disc._api_allowed_methods = {"/redfish/v1/A": ["GET", "HEAD"]}
    disc._http_status = {"/redfish/v1/A": 200, "/redfish/v1/Ghost": 404}
    disc._error_file_mapping = {"/redfish/v1/Ghost": str(tmp_path / "Ghost.error.json")}

    disc.save_url_file_mapping()

    loaded = np.load(tmp_path / "rest_api_map.npy", allow_pickle=True).item()
    assert loaded["url_file_mapping"] == disc._discovered_url_file_mapping
    assert loaded["allowed_methods_mapping"] == disc._api_allowed_methods
    assert loaded["http_status_mapping"] == disc._http_status
    assert loaded["error_file_mapping"] == disc._error_file_mapping


def test_save_url_file_mapping_keys_are_stable_for_igc(tmp_path):
    """The IGC contract keys ALWAYS exist, even on an empty crawl. igc's loader
    reads `url_file_mapping` / `allowed_methods_mapping` unconditionally; the new
    `http_status_mapping` / `error_file_mapping` are additive and must not
    displace them. An empty crawl still produces all keys, never a bare payload.
    """
    disc = _new_discovery(tmp_path)

    disc.save_url_file_mapping()

    loaded = np.load(tmp_path / "rest_api_map.npy", allow_pickle=True).item()
    # the two legacy IGC keys are present and stable...
    assert loaded["url_file_mapping"] == {}
    assert loaded["allowed_methods_mapping"] == {}
    # ...alongside the additive capture keys.
    assert set(loaded.keys()) == {
        "url_file_mapping", "allowed_methods_mapping",
        "http_status_mapping", "error_file_mapping",
    }
    assert loaded["http_status_mapping"] == {}
    assert loaded["error_file_mapping"] == {}
