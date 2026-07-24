"""Tests for the directory-walking Redfish schema validator helper."""

from __future__ import annotations

import json
from pathlib import Path

from tools import redfish_validate


class _FakeValidationError:
    def __init__(self, path: tuple[str, ...], message: str) -> None:
        self.absolute_path = path
        self.message = message


def test_validate_tree_classifies_valid_invalid_and_skipped_files(
    tmp_path: Path, monkeypatch
) -> None:
    """The tree helper reports every JSON file as valid, error, or skipped."""
    (tmp_path / "valid.json").write_text(json.dumps({"@odata.type": "#Valid.v1_0_0.Valid"}))
    (tmp_path / "invalid.json").write_text(
        json.dumps({"@odata.type": "#Invalid.v1_0_0.Invalid"})
    )
    (tmp_path / "missing-type.json").write_text(json.dumps({"Name": "metadata"}))
    (tmp_path / "broken.json").write_text("{")

    seen_strip_flags: list[bool] = []

    def fake_validate_payload(payload: dict, strip_oem: bool = True) -> list:
        seen_strip_flags.append(strip_oem)
        odata_type = payload.get("@odata.type")
        if not odata_type:
            raise ValueError("payload has no @odata.type")
        if "Invalid" in odata_type:
            return [_FakeValidationError(("Status", "State"), "is not valid")]
        return []

    monkeypatch.setattr(redfish_validate, "validate_payload", fake_validate_payload)

    summary = redfish_validate.validate_tree(tmp_path, strip_oem=False)

    assert summary["counts"] == {"files": 4, "valid": 1, "error": 2, "skipped": 1}
    assert summary["valid"] == ["valid.json"]
    assert summary["skipped"] == [
        {"path": "missing-type.json", "reason": "payload has no @odata.type"}
    ]
    assert {entry["path"] for entry in summary["error"]} == {"broken.json", "invalid.json"}
    assert seen_strip_flags == [False, False, False]


def test_main_returns_nonzero_and_prints_json_when_errors_exist(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The CLI returns failure for validation errors and can emit JSON output."""
    summary = {
        "root": str(tmp_path),
        "counts": {"files": 1, "valid": 0, "error": 1, "skipped": 0},
        "valid": [],
        "error": [{"path": "bad.json", "errors": ["Name: is required"]}],
        "skipped": [],
    }

    monkeypatch.setattr(redfish_validate, "validate_tree", lambda *_, **__: summary)

    exit_code = redfish_validate.main([str(tmp_path), "--json"])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == summary
