"""Offline conformance checks for declared vendor fixture claims."""
import json
from pathlib import Path

import pytest

from redfish_ctl.vendors import get_vendor

TESTS_ROOT = Path(__file__).resolve().parent
FIXTURE_ROOTS = {
    "dell": TESTS_ROOT / "dell_fixtures",
    "hpe": TESTS_ROOT / "hpe_fixtures",
    "supermicro": TESTS_ROOT / "supermicro_fixtures",
}


def _path_to_fixture_name(path: str) -> str:
    return "_" + path.strip("/").replace("/", "_") + ".json"


def _load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_for(root: Path, redfish_path: str) -> Path:
    return root / _path_to_fixture_name(redfish_path)


def _action_names(value) -> set[str]:
    if not isinstance(value, dict):
        return set()

    names = set()
    if "target" in value:
        return names

    for key, nested in value.items():
        if isinstance(nested, dict) and "target" in nested:
            names.add(key)
        names.update(_action_names(nested))
    return names


def _fixture_actions(root: Path) -> set[str]:
    actions = set()
    for path in root.glob("*.json"):
        payload = _load_fixture(path)
        actions.update(_action_names(payload.get("Actions", {})))
    return actions


@pytest.mark.parametrize("vendor", ("dell", "hpe", "supermicro"))
def test_declared_resource_roots_exist_in_vendor_fixtures(vendor):
    caps = get_vendor(vendor)
    root = FIXTURE_ROOTS[vendor]

    missing = [
        resource
        for resource in caps.supported_resources
        if not _fixture_for(root, resource).is_file()
    ]

    assert missing == []

@pytest.mark.parametrize("vendor", ("dell", "hpe", "supermicro"))
def test_declared_actions_exist_in_vendor_fixtures(vendor):
    caps = get_vendor(vendor)
    fixture_actions = _fixture_actions(FIXTURE_ROOTS[vendor])

    missing = [
        action
        for action in caps.supported_actions
        if action not in fixture_actions
    ]

    assert missing == []
