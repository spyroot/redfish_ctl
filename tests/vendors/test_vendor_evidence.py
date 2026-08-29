"""Offline tests for vendor capability evidence notes.

Author Mus spyroot@gmail.com
"""
from pathlib import Path

import pytest

from redfish_ctl.vendors import get_vendor
from redfish_ctl.vendors.base import VendorCapabilities

REPO_ROOT = Path(__file__).resolve().parents[2]


def _evidence_path(note: str) -> Path:
    """Return the first path token from a capability evidence note."""
    first_token = note.split()[0]
    return REPO_ROOT / first_token.rstrip(":")


def test_capabilities_evidence_defaults_to_empty_mapping():
    """Generic profiles can omit evidence until a capability is verified."""
    caps = VendorCapabilities(vendor="test")
    assert caps.evidence == {}


def test_dell_profile_records_evidence_for_non_default_claims():
    """Dell-only capability claims carry local fixture or source provenance."""
    evidence = get_vendor("dell").evidence
    assert evidence["query_select"].startswith("tests/idrac_fixtures/")
    assert evidence["query_filter"].startswith("tests/idrac_fixtures/")
    assert evidence["query_expand"].startswith("tests/idrac_fixtures/")
    assert evidence["query_top"].startswith("tests/idrac_fixtures/")
    assert evidence["query_only"].startswith("tests/idrac_fixtures/")
    assert evidence["one_query_param_per_uri"].startswith("tests/test_query.py")
    assert evidence["job_scheduling"].startswith("tests/idrac_fixtures/")
    assert evidence["lifecycle_events_sse"].startswith("tests/idrac_fixtures/")


def test_dell_evidence_paths_exist():
    """Every Dell evidence note starts with a committed file path."""
    for note in get_vendor("dell").evidence.values():
        assert _evidence_path(note).exists(), note


def test_evidence_mapping_is_immutable():
    """Evidence notes cannot be mutated through a shared vendor profile."""
    evidence = get_vendor("dell").evidence
    with pytest.raises(TypeError):
        evidence["query_select"] = "tests/test_vendors.py"  # type: ignore[index]
