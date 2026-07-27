"""Freeze the machine-readable flat-corpus conversion boundary."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "specs" / "sim" / "corpus-tree-conversion.yaml"


def _contract() -> dict:
    """Load the corpus-tree conversion contract."""
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_corpus_tree_conversion_routes_by_authority_not_filename_guessing():
    """The lossy flat filename is validation evidence, never a route decoder."""
    contract = _contract()
    recovery = contract["route_recovery"]

    assert contract["schema"] == "redfish_ctl.corpus_tree_conversion/v1"
    assert contract["purpose"]["directory_protocol_contract"] == (
        "specs/sim/dmtf-sim-contract.yaml"
    )
    assert contract["lifecycle"]["state"] == "implementation-candidate"
    assert contract["lifecycle"]["implementation_available"] is True
    assert contract["lifecycle"]["activation_status"] == (
        "pending-authoritative-ci"
    )
    assert "split_flat_filename_on_underscore" in recovery["forbidden"]
    assert [source["id"] for source in recovery["ordered_sources"]] == [
        "explicit-route-map",
        "top-level-odata-id",
        "discovered-odata-link",
        "service-root-anchor",
        "metadata-anchor",
    ]
    assert recovery["agreement"]["rule"].startswith("all available authoritative")
    assert recovery["agreement"]["disagreement"] == "blocking-conflict"
    alias_policy = recovery["agreement"]["canonical_alias_subset"]
    assert alias_policy["disposition"] == "record-excluded"
    assert alias_policy["emitted"] == "canonical-source-fixture-only"
    assert alias_policy["report_reason"] == "canonical-alias-subset"
    assert alias_policy["any_missing_evidence"] == "blocking-conflict"
    assert "alias-and-canonical-payloads-parse-to-equal-json" in alias_policy[
        "required_evidence"
    ]
    assert recovery["canonicalize_candidate"]["accept"]["scheme"] == "none"
    assert recovery["canonicalize_candidate"]["accept"]["authority"] == "none"
    assert {
        "empty_internal_segment",
        "dot_segment",
        "dot_dot_segment",
        "backslash",
        "nul",
        "encoded_path_separator",
        "malformed_percent_encoding",
    } <= set(recovery["canonicalize_candidate"]["reject"])
    assert recovery["canonicalize_candidate"]["relative_uri"]["disposition"] == (
        "blocking-unresolved"
    )
    assert "percent-decode-path-component-once" in recovery[
        "canonicalize_candidate"
    ]["normalize"]
    assert recovery["canonicalize_candidate"]["validation_order"][1] == (
        "reject-encoded-slash-or-backslash-before-decoding"
    )
    assert recovery["non_routes"]["fragment_selector"]["disposition"] == (
        "record-excluded"
    )
    assert recovery["non_routes"]["query_variant"]["disposition"] == (
        "blocking-unresolved"
    )
    assert recovery["non_routes"]["captured_fragment_file"]["disposition"] == (
        "record-excluded"
    )
    route_map = contract["inputs"]["fixtures"]["optional_route_map"]
    assert route_map["schema"] == "redfish_ctl.corpus_routes/v1"
    assert route_map["shape"]["routes"]["item_required_fields"] == [
        "route",
        "sourceFixture",
    ]
    sidecars = contract["inputs"]["fixtures"]["sidecars"]
    assert sidecars["disposition"] == "sidecar"
    assert "rest_api_map.npy" in sidecars["names"]
    assert "MUST NOT load" in sidecars["notes"]["rest_api_map.npy"]
    assert contract["inputs"]["archive"]["extraction"].startswith("none;")
    assert contract["inputs"]["archive"]["limits"] == {
        "members": 20000,
        "bytes_per_regular_file": 67108864,
        "total_uncompressed_bytes": 1073741824,
    }
    assert recovery["forward_filename_check"]["special"][
        "/redfish/v1/$metadata"
    ] == "_redfish_v1_$metadata.xml"


def test_corpus_tree_conversion_maps_redfish_paths_to_index_files():
    """The output tree matches the mockup server's request-path protocol."""
    contract = _contract()
    construction = contract["path_mapping"]["construction"]
    rules = {rule["route"]: rule for rule in contract["path_mapping"]["rules"]}

    assert construction["route_source"] == "route-recovery-authoritative-result"
    assert construction["filename_role"] == "validation-only"
    assert construction["algorithm"] == [
        "use-canonicalized-route-from-route-recovery",
        "remove-exact-prefix-/redfish/v1",
        "split-remaining-path-on-forward-slash",
        "create-one-directory-per-nonempty-segment",
        "write-payload-to-terminal-index-file",
    ]
    assert construction["resource_kind_exceptions"] == "none"
    assert rules["/redfish/v1"]["destination"] == "<profile-root>/index.json"
    assert rules["/redfish/v1/$metadata"]["destination"] == (
        "<profile-root>/$metadata/index.xml"
    )
    assert contract["path_mapping"]["example"] == {
        "request": "/redfish/v1/AccountService/Accounts",
        "source_fixture": "_redfish_v1_AccountService_Accounts.json",
        "destination": "<profile-root>/AccountService/Accounts/index.json",
    }
    assert contract["path_mapping"]["recursive_example"] == {
        "authoritative_odata_id": (
            "/redfish/v1/Systems/437XR1138R2/Bios/Settings"
        ),
        "segments": ["Systems", "437XR1138R2", "Bios", "Settings"],
        "destination": (
            "<profile-root>/Systems/437XR1138R2/Bios/Settings/index.json"
        ),
    }
    underscore_example = contract["path_mapping"]["underscore_segment_example"]
    assert underscore_example["destination"] == (
        "<profile-root>/Systems/HGX_Baseboard_0/index.json"
    )
    assert underscore_example["destination"] != underscore_example[
        "forbidden_destination"
    ]
    assert underscore_example["forward_filename_check"] == "match"
    assert contract["route_recovery"]["forward_filename_check"][
        "mismatch_example"
    ]["disposition"] == "blocking-conflict"
    assert rules["/redfish/v1/$metadata"]["content_type"] == "application/xml"
    assert contract["inputs"]["fixtures"]["metadata_xml_policy"] == (
        "optional-or-record-missing"
    )
    assert contract["payload_fidelity"]["xml"]["validation"] == "well-formed"
    assert contract["payload_fidelity"]["json"]["invalid_disposition"] == (
        "unresolved"
    )


def test_vendor_tree_conversion_is_fail_closed_and_not_dmtf_conformance():
    """A converted vendor capture is complete, deterministic, and honest."""
    contract = _contract()

    assert contract["purpose"]["profile_kind"] == "vendor-corpus"
    assert contract["purpose"]["conformance_claim"] == "none"
    assert {"dmtf-reference-profile", "dmtf-conformance"} <= set(
        contract["purpose"]["forbidden_claims"]
    )
    assert contract["accountability"]["silent_loss"] == "forbidden"
    assert contract["accountability"]["terminal_statuses"] == [
        "emitted",
        "excluded",
        "unresolved",
        "sidecar",
    ]
    assert contract["accountability"]["conflict_source_disposition"] == (
        "unresolved"
    )
    assert "conflictFiles" in contract["report"]["counts_fields"]
    assert contract["report"]["pass_conditions"]["conflicts"] == 0
    assert contract["report"]["pass_conditions"]["unresolved"] == 0
    assert contract["determinism"]["timestamps_in_output"] == "forbidden"
    assert contract["hashes"]["algorithm"] == "sha256"
    assert contract["hashes"]["source_input"] == "raw-compressed-tarball-bytes"
    assert contract["hashes"]["mapping_content"] == "raw-emitted-bytes"
    assert contract["hashes"]["tree_excludes"] == "conversion-report"
    assert contract["hashes"]["report_serialization"]["json_sort_keys"] is True
    assert contract["report"]["destination_base"] == "profile-root"
    assert contract["collisions"]["existing_output"] == {
        "identical_bytes": "allowed",
        "different_bytes": "blocking",
    }
    assert contract["collisions"]["multiple_source_fixtures_to_route"] == {
        "default": "blocking-for-every-claimant",
        "exception": (
            "canonical-alias-subset-excluded-before-collision-evaluation"
        ),
    }
    assert contract["payload_fidelity"]["rewriting"]["vendor_oem_data"] == (
        "forbidden"
    )
    assert contract["implementation"]["commands"]["materialize"][
        "precondition"
    ] == "the same input plan report has result=pass"
    assert contract["implementation"]["exit_codes"] == {
        0: "report result is pass",
        1: "report result is fail",
        2: "input, environment, or existing-output precondition error",
    }
    dell_finding = contract["known_source_findings"][0]
    assert dell_finding["id"] == "dell-xr8620t-canonical-alias-subset"
    assert dell_finding["result"] == "pass"
    assert dell_finding["counts"]["excluded_canonical_alias_files"] == 44
    assert dell_finding["counts"]["unresolved_files"] == 0
    assert dell_finding["alias_payload_comparison"] == {
        "byte_identical": 41,
        "json_equivalent": 3,
    }
