from pathlib import Path

import pytest

from redfish_ctl.fixtures_catalog import (
    DEFAULT_MANIFEST,
    CatalogError,
    FixtureCatalog,
    load_catalog,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_default_fixture_catalog_lists_known_active_roots():
    catalog = load_catalog()

    assert catalog.require("dell-idrac-overlay").vendor == "dell"
    assert catalog.require("generic-dmtf-public-rackmount1").vendor == "generic"
    assert catalog.require("supermicro-gb300-corpus").generation == "gb300"


def test_active_fixture_roots_resolve_to_non_empty_json_trees():
    catalog = load_catalog()

    active = catalog.active_sets()
    assert active
    for fixture_set in active:
        root = fixture_set.resolve_path(REPO_ROOT)
        assert root.is_dir(), fixture_set.key
        assert fixture_set.json_file_count(REPO_ROOT) > 0, fixture_set.key


def test_pending_fixture_sets_are_explicitly_reported():
    catalog = load_catalog()

    pending = {fixture_set.key: fixture_set for fixture_set in catalog.pending_sets()}
    assert "cisco-cimc-corpus" in pending
    assert "lenovo-xcc-doc-seeded" in pending
    assert all(fixture_set.reason for fixture_set in pending.values())


def test_catalog_lookup_fails_loudly_for_unknown_key():
    catalog = load_catalog()

    with pytest.raises(CatalogError, match="unknown fixture set"):
        catalog.require("not-a-fixture-set")


def test_catalog_rejects_duplicate_keys(tmp_path):
    manifest = tmp_path / "fixtures.json"
    manifest.write_text(
        """
        {
          "schema_version": 1,
          "sets": [
            {
              "key": "duplicate",
              "vendor": "generic",
              "generation": "one",
              "path": "tests/generic_fixtures",
              "status": "active"
            },
            {
              "key": "duplicate",
              "vendor": "generic",
              "generation": "two",
              "path": "tests/generic_fixtures",
              "status": "active"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(CatalogError, match="duplicate fixture set key"):
        FixtureCatalog.from_manifest(manifest)


def test_default_manifest_is_repo_relative():
    assert DEFAULT_MANIFEST == REPO_ROOT / "tests" / "fixtures_catalog.json"
