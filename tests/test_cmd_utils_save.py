"""Offline unit tests for redfish_ctl.cmd_utils.save_if_needed.

These are regressions for two bugs:

  1. the YAML branch dumped the *filename* instead of the payload, so
     ``--format yaml`` wrote garbage.
  2. the unknown-format branch built a ``ValueError`` but never raised it,
     so a bad format silently produced no output.

Author Mus spyroot@gmail.com
"""
import json
from pathlib import Path

import pytest
import yaml

from redfish_ctl.cmd_utils import save_if_needed


def test_save_json_round_trips_payload(tmp_path: Path):
    """JSON branch writes the payload and appends the .json suffix."""
    payload = {"k": "v", "n": 1}
    target = tmp_path / "out"

    save_if_needed(str(target), payload, data_format="json")

    written = tmp_path / "out.json"
    assert written.exists()
    assert json.loads(written.read_text()) == payload


def test_save_json_keeps_existing_suffix(tmp_path: Path):
    """An explicit .json filename is not double-suffixed."""
    payload = {"a": 1}
    target = tmp_path / "result.json"

    save_if_needed(str(target), payload, data_format="json")

    assert target.exists()
    assert not (tmp_path / "result.json.json").exists()
    assert json.loads(target.read_text()) == payload


def test_save_yaml_writes_payload_not_filename(tmp_path: Path):
    """Regression: YAML branch must serialize raw_data, not the filename."""
    payload = {"MemFrequency": "MaxPerf", "ProcCStates": "Disabled"}
    target = tmp_path / "bios"

    save_if_needed(str(target), payload, data_format="yaml")

    written = tmp_path / "bios.yaml"
    assert written.exists()
    loaded = yaml.safe_load(written.read_text())
    assert loaded == payload
    # The old bug serialized the filename string; guard against regressing.
    assert loaded != str(target)


def test_unknown_format_raises_value_error(tmp_path: Path):
    """Regression: an unsupported format must raise, not silently no-op."""
    with pytest.raises(ValueError):
        save_if_needed(str(tmp_path / "x"), {"a": 1}, data_format="toml")


def test_empty_filename_is_noop(tmp_path: Path):
    """No filename means nothing is written and nothing raises."""
    save_if_needed("", {"a": 1}, data_format="json")
    save_if_needed(None, {"a": 1}, data_format="json")
    assert list(tmp_path.iterdir()) == []


def test_none_payload_is_noop(tmp_path: Path):
    """No payload means nothing is written and nothing raises."""
    target = tmp_path / "out.json"
    save_if_needed(str(target), None, data_format="json")
    assert not target.exists()


def test_directory_target_warns_and_skips(tmp_path: Path):
    """Pointing at a directory warns and does not write."""
    with pytest.warns(UserWarning):
        save_if_needed(str(tmp_path), {"a": 1}, data_format="json")
