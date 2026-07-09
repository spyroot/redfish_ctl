"""Offline tests for the discovery output producer (the igc .npy contract).

`Discovery.save_url_file_mapping` writes `rest_api_map.npy` — the artifact the
igc project consumes via `np.load(..., allow_pickle=True).item()`. These tests
pin the file name and the two top-level keys so a refactor cannot silently break
that cross-repo contract. No iDRAC, no network.
"""
import numpy as np

from redfish_ctl.discovery.cmd_discovery import Discovery


def test_save_url_file_mapping_roundtrip(tmp_path):
    """save_url_file_mapping writes rest_api_map.npy that round-trips to its inputs."""
    disc = Discovery.__new__(Discovery)
    disc.json_response_dir = str(tmp_path)
    disc._discovered_url_file_mapping = {"/redfish/v1/A": str(tmp_path / "A.json")}
    disc._api_allowed_methods = {"/redfish/v1/A": ["GET", "HEAD"]}

    disc.save_url_file_mapping()

    loaded = np.load(tmp_path / "rest_api_map.npy", allow_pickle=True).item()
    assert set(loaded.keys()) == {"url_file_mapping", "allowed_methods_mapping"}
    assert loaded["url_file_mapping"] == disc._discovered_url_file_mapping
    assert loaded["allowed_methods_mapping"] == disc._api_allowed_methods


def test_save_url_file_mapping_keys_are_stable_for_igc(tmp_path):
    """The two contract keys exist even when nothing was discovered (empty maps).

    igc's loader reads `url_file_mapping` / `allowed_methods_mapping` unconditionally;
    an empty crawl must still produce both keys, not a bare/None payload.
    """
    disc = Discovery.__new__(Discovery)
    disc.json_response_dir = str(tmp_path)
    disc._discovered_url_file_mapping = {}
    disc._api_allowed_methods = {}

    disc.save_url_file_mapping()

    loaded = np.load(tmp_path / "rest_api_map.npy", allow_pickle=True).item()
    assert loaded["url_file_mapping"] == {}
    assert loaded["allowed_methods_mapping"] == {}
