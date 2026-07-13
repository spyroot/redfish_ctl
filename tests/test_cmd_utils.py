"""Offline unit tests for the pure helpers in redfish_ctl.cmd_utils.

Author Mus spyroot@gmail.com
"""
import argparse
import json
from pathlib import Path

import pytest

from redfish_ctl.cmd_utils import find_ids, from_json_spec, save_if_needed, str2bool


def test_from_json_spec_reads_file(tmp_path: Path):
    """from_json_spec parses JSON from a file path."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"Attributes": {"BootMode": "Uefi"}}))
    assert from_json_spec(str(spec)) == {"Attributes": {"BootMode": "Uefi"}}


def test_find_ids_walks_nested_structures():
    """find_ids collects every value for a key across nested dicts/lists."""
    obj = {
        "Members": [
            {"@odata.id": "/redfish/v1/Systems/1"},
            {"@odata.id": "/redfish/v1/Systems/2"},
        ],
        "nested": {"@odata.id": "/redfish/v1/Managers/1"},
    }
    found = find_ids(obj, "@odata.id")
    assert "/redfish/v1/Managers/1" in found
    assert len(found) >= 1


def test_find_ids_empty_when_absent():
    """find_ids returns an empty list when the key is missing."""
    assert find_ids({"a": 1}, "nope") == []


def test_find_ids_handles_none():
    """find_ids tolerates a None object without raising."""
    assert find_ids(None, "x") == []


@pytest.mark.parametrize("value", ["yes", "true", "t", "y", "1", "TRUE", "Y"])
def test_str2bool_truthy(value):
    """str2bool accepts the documented truthy spellings."""
    assert str2bool(value) is True


@pytest.mark.parametrize("value", ["no", "false", "f", "n", "0", "FALSE"])
def test_str2bool_falsy(value):
    """str2bool accepts the documented falsy spellings."""
    assert str2bool(value) is False


def test_str2bool_passes_through_bool():
    """str2bool returns a bool argument unchanged."""
    assert str2bool(True) is True
    assert str2bool(False) is False


def test_str2bool_rejects_garbage():
    """str2bool raises argparse error on an unrecognized value."""
    with pytest.raises(argparse.ArgumentTypeError):
        str2bool("maybe")


def test_save_if_needed_creates_missing_save_dir(tmp_path: Path):
    """A non-existent save_dir is created rather than raising."""
    target_dir = tmp_path / "out" / "nested"
    assert not target_dir.exists()
    save_if_needed("result", {"a": 1}, data_format="json", save_dir=str(target_dir))
    assert target_dir.exists() and target_dir.is_dir()
