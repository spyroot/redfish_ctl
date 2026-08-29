"""Validate the committed BIOS tuning profiles under specs/profiles/.

Each profile declares BIOS attribute overrides. These tests guard the
"no invented knobs" rule: every attribute a profile sets must exist in the
matching vendor's committed BIOS attribute registry, and the value must be
allowable there. The profiles feed the ``bios-profile`` catalog reader.

The registries are real captures committed to the repo, so this runs fully
offline with no iDRAC and no network.
"""

import json
from pathlib import Path

import pytest
from vendor_corpus import corpus_dir

REPO = Path(__file__).resolve().parents[2]
PROFILE_DIR = REPO / "specs" / "profiles"

# vendor -> committed BIOS attribute registry captured from real hardware.
REGISTRY_BY_VENDOR = {
    "supermicro": (
        corpus_dir(REPO / "tests" / "supermicro_gb300_corpus.tar.gz", "172.25.230.37")
        / "_redfish_v1_Registries_BiosAttributeRegistry_BiosAttributeRegistry.json"
    ),
    "dell": (
        REPO
        / "tests"
        / "idrac_fixtures"
        / "_redfish_v1_Systems_System.Embedded.1_Bios_BiosRegistry.json"
    ),
}

REQUIRED_KEYS = {"name", "vendor", "model", "description", "risk", "attributes"}


def _load_registry(path):
    """Return {AttributeName: attribute-entry} from a BIOS registry capture."""
    data = json.loads(path.read_text())
    entries = data.get("RegistryEntries", {}).get("Attributes", [])
    return {a["AttributeName"]: a for a in entries if "AttributeName" in a}


def _profile_files():
    """All committed profile JSON files (excludes the README)."""
    return sorted(PROFILE_DIR.glob("*.json"))


def test_profiles_present():
    """specs/profiles/ exists and holds at least one profile (unblocks the reader)."""
    assert _profile_files(), "no profiles found under specs/profiles/"


@pytest.mark.parametrize("path", _profile_files(), ids=lambda p: p.name)
def test_profile_schema_and_grounded(path):
    """Each profile parses, carries the required keys, and sets only real knobs.

    Edge cases covered: a value outside a knob's allowable set, a wrong python
    type for a boolean/integer knob, and an attribute absent from the registry
    (the classic "invented knob") all fail here rather than at apply time on
    real hardware.
    """
    profile = json.loads(path.read_text())

    missing = REQUIRED_KEYS - profile.keys()
    assert not missing, f"{path.name} missing keys: {sorted(missing)}"
    assert path.stem == profile["name"], (
        f"{path.name}: filename stem must equal profile name {profile['name']!r}"
    )
    assert profile["risk"] in {"low", "medium", "high"}, (
        f"{path.name}: risk must be low/medium/high"
    )

    vendor = profile["vendor"]
    assert vendor in REGISTRY_BY_VENDOR, (
        f"{path.name}: no committed BIOS registry pinned for vendor {vendor!r}"
    )
    registry = _load_registry(REGISTRY_BY_VENDOR[vendor])

    attributes = profile["attributes"]
    assert attributes, f"{path.name}: profile sets no attributes"
    for attr_name, value in attributes.items():
        assert attr_name in registry, (
            f"{path.name}: {attr_name!r} is not in the {vendor} BIOS registry "
            "(no invented knobs)"
        )
        entry = registry[attr_name]
        atype = entry.get("Type")
        if atype == "Enumeration":
            allowed = {v.get("ValueName") for v in entry.get("Value", [])}
            assert value in allowed, (
                f"{path.name}: {attr_name}={value!r} not in allowable {sorted(allowed)}"
            )
        elif atype == "Boolean":
            # bool is a subclass of int, so check bool explicitly.
            assert isinstance(value, bool), (
                f"{path.name}: {attr_name} must be a boolean"
            )
        elif atype == "Integer":
            assert isinstance(value, int) and not isinstance(value, bool), (
                f"{path.name}: {attr_name} must be an integer"
            )
            lower, upper = entry.get("LowerBound"), entry.get("UpperBound")
            if lower is not None:
                assert value >= lower, f"{path.name}: {attr_name}={value} < {lower}"
            if upper is not None:
                assert value <= upper, f"{path.name}: {attr_name}={value} > {upper}"
