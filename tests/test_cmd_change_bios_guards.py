"""Regression tests for cmd_change_bios.execute() registry-guard hardening.

Covers the corner cases around fetching the BIOS registry: missing/None data,
a non-dict RegistryEntries, missing Attributes (the original PR #5 bug fix),
a non-list Attributes, the JSON-spec short-circuit, and that an invalid spec
surfaces the decoder detail.

Author Mus spyroot@gmail.com
"""
import json

import pytest

from redfish_ctl.bios.cmd_change_bios import BiosChangeSettings
from redfish_ctl.cmd_exceptions import InvalidJsonSpec
from redfish_ctl.redfish_manager import CommandResult


@pytest.fixture
def bios_cmd():
    """A BiosChangeSettings command instance (offline; no iDRAC contacted)."""
    return BiosChangeSettings(
        idrac_ip="mock-idrac", idrac_username="root",
        idrac_password="mock", insecure=True, is_debug=False,
    )


def _serve_registry(monkeypatch, cmd, data):
    """Make the command's registry query return ``data``."""
    monkeypatch.setattr(
        cmd, "base_query", lambda *a, **k: CommandResult(data, None, None, None)
    )


def test_none_registry_data_fails_gracefully(bios_cmd, monkeypatch):
    """No data from the registry query -> graceful failure, not a TypeError."""
    _serve_registry(monkeypatch, bios_cmd, None)
    result = bios_cmd.execute(attr_name="MemTest", attr_value="Disabled", do_show=True)
    assert result.data["Status"] == "Failed fetch bios registry"


def test_missing_registry_entries(bios_cmd, monkeypatch):
    """Response without RegistryEntries -> attribute fetch failure."""
    _serve_registry(monkeypatch, bios_cmd, {"@odata.id": "/x"})
    result = bios_cmd.execute(attr_name="MemTest", attr_value="Disabled", do_show=True)
    assert result.data["Status"] == "Failed fetch attributes from bios registry"


def test_registry_entries_not_a_dict(bios_cmd, monkeypatch):
    """RegistryEntries that is a list -> graceful failure (no crash on indexing)."""
    _serve_registry(monkeypatch, bios_cmd, {"RegistryEntries": ["nope"]})
    result = bios_cmd.execute(attr_name="MemTest", attr_value="Disabled", do_show=True)
    assert result.data["Status"] == "Failed fetch attributes from bios registry"


def test_missing_attributes_is_the_pr5_path(bios_cmd, monkeypatch):
    """RegistryEntries present but no Attributes -> the original PR #5 failure path."""
    _serve_registry(monkeypatch, bios_cmd, {"RegistryEntries": {"Menus": []}})
    result = bios_cmd.execute(attr_name="MemTest", attr_value="Disabled", do_show=True)
    assert result.data["Status"] == "Failed fetch attributes from bios registry"


def test_attributes_not_a_list(bios_cmd, monkeypatch):
    """Attributes present but not a list -> malformed, not a downstream crash."""
    _serve_registry(monkeypatch, bios_cmd, {"RegistryEntries": {"Attributes": None}})
    result = bios_cmd.execute(attr_name="MemTest", attr_value="Disabled", do_show=True)
    assert result.data["Status"] == "Bios registry attributes are malformed"


def test_valid_registry_builds_payload(bios_cmd, monkeypatch):
    """A valid registry yields the requested change (the PR #5 fix actually works)."""
    registry = {"RegistryEntries": {"Attributes": [
        {"AttributeName": "MemTest", "Type": "Enumeration"},
    ]}}
    _serve_registry(monkeypatch, bios_cmd, registry)
    monkeypatch.setattr(bios_cmd, "create_apply_time_req", lambda *a, **k: {})
    result = bios_cmd.execute(attr_name="MemTest", attr_value="Disabled", do_show=True)
    assert result.data["Attributes"]["MemTest"] == "Disabled"


def test_from_spec_skips_registry_fetch(bios_cmd, monkeypatch, tmp_path):
    """With --from_spec, the BIOS registry is not fetched at all."""
    calls = {"n": 0}

    def _spy(*a, **k):
        calls["n"] += 1
        return CommandResult({}, None, None, None)

    monkeypatch.setattr(bios_cmd, "base_query", _spy)
    monkeypatch.setattr(bios_cmd, "create_apply_time_req", lambda *a, **k: {})
    spec = tmp_path / "bios.json"
    spec.write_text(json.dumps({"Attributes": {"MemTest": "Disabled"}}))
    result = bios_cmd.execute(from_spec=str(spec), do_show=True)
    assert calls["n"] == 0
    assert result.data["Attributes"]["MemTest"] == "Disabled"


def test_invalid_spec_surfaces_decoder_detail(bios_cmd, monkeypatch, tmp_path):
    """An invalid JSON spec raises InvalidJsonSpec with the decoder detail."""
    monkeypatch.setattr(bios_cmd, "create_apply_time_req", lambda *a, **k: {})
    spec = tmp_path / "bad.json"
    spec.write_text("{not valid json")
    with pytest.raises(InvalidJsonSpec) as exc:
        bios_cmd.execute(from_spec=str(spec), do_show=True)
    assert "JSONlint" in str(exc.value)
