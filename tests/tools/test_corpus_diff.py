"""Offline tests for the vendor-generic corpus diff engine (tools/corpus_diff.py).

Covers the discovery walk on a synthetic Dell-shaped tree (member ids the old
hardcoded tool could NOT handle), drift and gap classification, the dry-run
plan, bounded output, and — when the LFS tarballs are pulled — a self-check of
every committed vendor corpus, which proves the walk is vendor-generic across
the real Dell/HPE/Supermicro/NVIDIA layouts. No network, no credentials.

Author Mus spyroot@gmail.com
"""
import json

import pytest
from vendor_corpus import corpus_dir as extract_corpus

from tools import corpus, corpus_diff


def _write(directory, path, payload):
    """Write one flattened corpus fixture for ``path`` into ``directory``.

    :param directory: corpus directory under construction.
    :param path: Redfish resource path the fixture serves.
    :param payload: JSON-serializable resource body.
    """
    (directory / corpus_diff.fixture_name(path)).write_text(json.dumps(payload))


def _dell_shaped_corpus(directory):
    """Build a minimal Dell-shaped corpus tree (System.Embedded.1 member ids).

    The member ids deliberately differ from the Supermicro ``System_0``/``BMC_0``
    shape so a hardcoded-path walker would find nothing — passing this tree
    proves the discovery is generic.

    :param directory: empty directory to fill with flattened fixtures.
    """
    _write(directory, "/redfish/v1", {
        "Vendor": "Dell", "Product": "iDRAC", "RedfishVersion": "1.15.1",
        "Systems": {"@odata.id": "/redfish/v1/Systems"},
        "Managers": {"@odata.id": "/redfish/v1/Managers"},
    })
    _write(directory, "/redfish/v1/Systems", {
        "Members": [{"@odata.id": "/redfish/v1/Systems/System.Embedded.1"}]})
    _write(directory, "/redfish/v1/Systems/System.Embedded.1", {
        "Manufacturer": "Dell Inc.", "Model": "XR8620t", "BiosVersion": "2.3.4",
        "Boot": {"BootSourceOverrideTarget@Redfish.AllowableValues": ["None", "Pxe"]},
        "Bios": {"@odata.id": "/redfish/v1/Systems/System.Embedded.1/Bios"},
    })
    _write(directory, "/redfish/v1/Systems/System.Embedded.1/Bios", {
        "Attributes": {"BootMode": "Uefi", "ProcTurboMode": "Enabled"}})
    _write(directory, "/redfish/v1/Managers", {
        "Members": [{"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"}]})
    _write(directory, "/redfish/v1/Managers/iDRAC.Embedded.1", {
        "Manufacturer": "Dell Inc.", "Model": "iDRAC9",
        "FirmwareVersion": "7.10.30.00", "ManagerType": "BMC"})


def test_self_diff_of_dell_shaped_tree_is_all_match(tmp_path):
    """A Dell-shaped tree self-diffs 100% match — discovery is not hardcoded.

    The old tool hardcoded Supermicro ``System_0``/``BMC_0`` paths; this tree
    only answers Dell ids, so full coverage here is the genericity proof.
    """
    _dell_shaped_corpus(tmp_path)
    fetch = corpus_diff.corpus_fetcher(tmp_path)
    report = corpus_diff.compare(fetch, fetch)
    summary = report["summary"]
    assert summary["ok"] and summary["drift"] == 0 and summary["gaps"] == 0
    checked_paths = {row["path"] for row in report["rows"]}
    assert "/redfish/v1/Systems/System.Embedded.1" in checked_paths
    assert "/redfish/v1/Systems/System.Embedded.1/Bios" in checked_paths
    assert "/redfish/v1/Managers/iDRAC.Embedded.1" in checked_paths


def test_drift_in_stable_fields_and_member_set_is_detected(tmp_path):
    """A changed BiosVersion, BIOS key set, and member set all report drift.

    These are the config changes the tool exists to catch: reference corpus on
    one side, a re-flashed / re-configured box on the other.
    """
    ref, live = tmp_path / "ref", tmp_path / "live"
    ref.mkdir(), live.mkdir()
    _dell_shaped_corpus(ref)
    _dell_shaped_corpus(live)
    system_path = "/redfish/v1/Systems/System.Embedded.1"
    _write(live, system_path, {
        "Manufacturer": "Dell Inc.", "Model": "XR8620t", "BiosVersion": "9.9.9",
        "Boot": {"BootSourceOverrideTarget@Redfish.AllowableValues": ["None", "Pxe"]},
        "Bios": {"@odata.id": f"{system_path}/Bios"},
    })
    _write(live, f"{system_path}/Bios", {"Attributes": {"BootMode": "Uefi"}})
    _write(live, "/redfish/v1/Managers", {"Members": []})
    report = corpus_diff.compare(corpus_diff.corpus_fetcher(live),
                                 corpus_diff.corpus_fetcher(ref))
    drifted = {(r["path"], r["field"]) for r in report["rows"]
               if r["status"] == "drift"}
    assert (system_path, "BiosVersion") in drifted
    assert (f"{system_path}/Bios", "Attributes#keys") in drifted
    assert ("/redfish/v1/Managers", "Members#ids") in drifted
    assert not report["summary"]["ok"]


def test_gaps_are_reported_but_do_not_fail(tmp_path):
    """A resource missing on one side is a gap row, never drift.

    A live box that does not serve the Bios resource (or is unreachable for
    one GET) must not fail the whole comparison — unreachable is not "changed".
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _dell_shaped_corpus(ref)
    ref_fetch = corpus_diff.corpus_fetcher(ref)
    bios = "/redfish/v1/Systems/System.Embedded.1/Bios"

    def live_fetch(path):
        """Serve the reference tree except the Bios resource.

        :param path: Redfish resource path.
        :return: the reference fixture, or None for the Bios resource.
        """
        return None if path == bios else ref_fetch(path)

    report = corpus_diff.compare(live_fetch, ref_fetch)
    gap_rows = [r for r in report["rows"] if r["status"] == "live_gap"]
    assert [r["path"] for r in gap_rows] == [bios]
    assert report["summary"]["ok"] and report["summary"]["gaps"] == 1


def test_plan_is_corpus_side_only(tmp_path):
    """The dry-run plan discovers every path without any live-side fetch."""
    _dell_shaped_corpus(tmp_path)
    paths = corpus_diff.plan(corpus_diff.corpus_fetcher(tmp_path))
    assert paths[0] == "/redfish/v1"
    assert "/redfish/v1/Systems/System.Embedded.1/Bios" in paths
    assert "/redfish/v1/Managers/iDRAC.Embedded.1" in paths


def test_detail_output_is_bounded(tmp_path):
    """A huge BIOS key-set diff stays within the bounded-output budget.

    Agents pay per token: one drift row must never dump a 1000-key registry.
    """
    ref, live = tmp_path / "ref", tmp_path / "live"
    ref.mkdir(), live.mkdir()
    _dell_shaped_corpus(ref)
    _dell_shaped_corpus(live)
    bios = "/redfish/v1/Systems/System.Embedded.1/Bios"
    _write(live, bios, {"Attributes": {f"Key{i}": i for i in range(1000)}})
    report = corpus_diff.compare(corpus_diff.corpus_fetcher(live),
                                 corpus_diff.corpus_fetcher(ref))
    row = next(r for r in report["rows"]
               if r["path"] == bios and r["status"] == "drift")
    assert len(row["detail"]) < 500


def test_corpus_fetcher_is_case_insensitive_and_json_safe(tmp_path):
    """Odd fixture-name casing still resolves; a corrupt fixture yields None."""
    (tmp_path / "_REDFISH_v1.json").write_text(json.dumps({"Vendor": "X"}))
    (tmp_path / "_redfish_v1_Managers.json").write_text("{not json")
    fetch = corpus_diff.corpus_fetcher(tmp_path)
    assert fetch("/redfish/v1") == {"Vendor": "X"}
    assert fetch("/redfish/v1/Managers") is None
    assert fetch("/redfish/v1/Systems") is None


def test_boot_allowable_values_drift_is_detected(tmp_path):
    """A changed boot AllowableValues list reports drift, not a silent pass.

    Regression: the field path contains a DOT inside one property name
    (``BootSourceOverrideTarget@Redfish.AllowableValues``); a dot-split walker
    dug past the wrong keys, saw absent-on-both-sides, and counted it as a
    match — the comparison was silently dead for exactly this field.
    """
    ref, live = tmp_path / "ref", tmp_path / "live"
    ref.mkdir(), live.mkdir()
    _dell_shaped_corpus(ref)
    _dell_shaped_corpus(live)
    system_path = "/redfish/v1/Systems/System.Embedded.1"
    _write(live, system_path, {
        "Manufacturer": "Dell Inc.", "Model": "XR8620t", "BiosVersion": "2.3.4",
        "Boot": {"BootSourceOverrideTarget@Redfish.AllowableValues": ["None"]},
        "Bios": {"@odata.id": f"{system_path}/Bios"},
    })
    report = corpus_diff.compare(corpus_diff.corpus_fetcher(live),
                                 corpus_diff.corpus_fetcher(ref))
    boot_field = "Boot/BootSourceOverrideTarget@Redfish.AllowableValues"
    boot_rows = [r for r in report["rows"]
                 if r["path"] == system_path and r["field"] == boot_field]
    assert boot_rows and boot_rows[0]["status"] == "drift"
    # And in the untouched self-diff the SAME field must be actively verified:
    self_report = corpus_diff.compare(corpus_diff.corpus_fetcher(ref),
                                      corpus_diff.corpus_fetcher(ref))
    self_row = next(r for r in self_report["rows"]
                    if r["path"] == system_path and r["field"] == boot_field)
    assert self_row["status"] == "match"


def test_field_absent_on_both_sides_is_not_a_match(tmp_path):
    """A field neither side has counts as ``not_present``, never verified.

    Counting absent-vs-absent as a match inflates the verified-field count and
    hides a dead comparison — the exact failure mode of the dot-split bug.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _dell_shaped_corpus(ref)
    manager = "/redfish/v1/Managers/iDRAC.Embedded.1"
    body = json.loads((ref / corpus_diff.fixture_name(manager)).read_text())
    del body["ManagerType"]
    _write(ref, manager, body)
    fetch = corpus_diff.corpus_fetcher(ref)
    report = corpus_diff.compare(fetch, fetch)
    row = next(r for r in report["rows"]
               if r["path"] == manager and r["field"] == "ManagerType")
    assert row["status"] == "not_present"
    assert report["summary"]["not_present"] == 1
    assert report["summary"]["ok"]


def test_paginated_collection_refuses_a_green_result(tmp_path):
    """A collection carrying ``Members@odata.nextLink`` cannot report ok.

    Page one is not the full member set: comparing it silently would
    misrepresent drift status, so the row is flagged and ``ok`` goes False
    (the CLI maps it to the usage/environment exit, not to drift).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _dell_shaped_corpus(ref)
    body = json.loads((ref / corpus_diff.fixture_name("/redfish/v1/Systems")).read_text())
    body["Members@odata.nextLink"] = "/redfish/v1/Systems?$skip=1"
    _write(ref, "/redfish/v1/Systems", body)
    fetch = corpus_diff.corpus_fetcher(ref)
    report = corpus_diff.compare(fetch, fetch)
    paginated = [r for r in report["rows"] if r["status"] == "paginated"]
    assert paginated and paginated[0]["path"] == "/redfish/v1/Systems"
    assert report["summary"]["paginated"] == 2  # flagged on both sides
    assert not report["summary"]["ok"]


def test_plan_is_empty_without_a_service_root(tmp_path):
    """An empty corpus dir yields an EMPTY plan, not fallback paths.

    The fallback collection paths must not fabricate a plan out of nothing —
    the CLI keys its 'discovered nothing' exit-2 guard on plan emptiness.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    assert corpus_diff.plan(corpus_diff.corpus_fetcher(empty)) == []


def test_case_colliding_fixtures_are_refused(tmp_path):
    """Two fixtures differing only by name case raise instead of shadowing.

    The index is case-insensitive; letting glob order pick a silent winner
    would make the comparison nondeterministic across filesystems.
    """
    (tmp_path / "_redfish_v1.json").write_text(json.dumps({"Vendor": "A"}))
    (tmp_path / "_REDFISH_V1.json").write_text(json.dumps({"Vendor": "B"}))
    with pytest.raises(ValueError, match="case-colliding"):
        corpus_diff.corpus_fetcher(tmp_path)


def _tar_corpus(tmp_path, arcname, builder):
    """Pack a synthetic corpus into a ``.tar.gz`` shaped like the real ones.

    :param tmp_path: test temp root.
    :param arcname: internal root directory name (the manifest ``arcname``).
    :param builder: callable filling a directory with flattened fixtures.
    :return: path to the created tarball.
    """
    import tarfile
    tree = tmp_path / "tree" / arcname
    tree.mkdir(parents=True)
    builder(tree)
    tarball = tmp_path / "corpus.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(tree, arcname=arcname)
    return tarball


def test_cli_self_check_emits_one_json_document(tmp_path, monkeypatch, capsys):
    """`self-check` stdout is exactly ONE parseable JSON document (exit 0).

    Concatenated per-corpus documents broke every JSON consumer of the
    documented default invocation.
    """
    tarball = _tar_corpus(tmp_path, "10.0.0.1", _dell_shaped_corpus)
    row = {"vendor": "dell", "model": "fake", "tarball": tarball.name,
           "arcname": "10.0.0.1", "json_count": 6}
    monkeypatch.setattr(corpus, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(corpus, "load_manifest", lambda *a, **k: [row, row])
    code = corpus.main(["self-check"])
    out = capsys.readouterr()
    document = json.loads(out.out)  # would raise on concatenated documents
    assert code == 0
    assert document["mode"] == "self-check"
    assert document["summary"]["corpora_checked"] == 2
    assert document["summary"]["ok"]


def test_cli_self_check_all_skipped_is_exit_2_with_json(tmp_path, monkeypatch, capsys):
    """Every corpus skipped (pointer/missing) = exit 2 and an explicit document.

    A fresh clone without ``git lfs pull`` must not get a green self-check
    that verified nothing.
    """
    pointer = tmp_path / "p.tar.gz"
    pointer.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
    rows = [
        {"vendor": "a", "model": "ptr", "tarball": "p.tar.gz", "arcname": "x"},
        {"vendor": "b", "model": "gone", "tarball": "missing.tar.gz", "arcname": "x"},
    ]
    monkeypatch.setattr(corpus, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(corpus, "load_manifest", lambda *a, **k: rows)
    code = corpus.main(["self-check"])
    out = capsys.readouterr()
    document = json.loads(out.out)
    assert code == 2
    assert document["summary"]["corpora_checked"] == 0
    assert len(document["skipped"]) == 2
    assert not document["summary"]["ok"]


def test_cli_live_diff_missing_tarball_is_exit_2(tmp_path, monkeypatch, capsys):
    """A manifest row whose tarball file is missing exits 2, not a traceback.

    ``FileNotFoundError`` escaping as exit 1 would collide with the drift
    exit code and hide an environment problem as a finding.
    """
    rows = [{"vendor": "a", "model": "gone", "tarball": "no.tar.gz", "arcname": "x"}]
    monkeypatch.setattr(corpus, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(corpus, "load_manifest", lambda *a, **k: rows)
    code = corpus.main(["live-diff", "--vendor", "a", "--model", "gone",
                        "--ip", "203.0.113.5"])
    assert code == 2
    assert "missing" in capsys.readouterr().err


def test_cli_live_diff_dry_run_document_shape_is_stable(tmp_path, monkeypatch, capsys):
    """Dry-run and real runs share one top-level key set (dry_run flags which).

    A consumer must be able to key on the same fields in both modes instead of
    guessing which keys exist.
    """
    corpus_dir = tmp_path / "extracted"
    corpus_dir.mkdir()
    _dell_shaped_corpus(corpus_dir)
    code = corpus.main(["live-diff", "--corpus-dir", str(corpus_dir),
                        "--ip", "203.0.113.5", "--dry-run"])
    document = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(document) == {"mode", "dry_run", "corpus", "target",
                             "plan", "summary", "rows"}
    assert document["dry_run"] is True and document["plan"]
    assert document["summary"] is None and document["rows"] == []


def test_cli_live_diff_empty_corpus_dir_is_exit_2(tmp_path, capsys):
    """An existing-but-empty --corpus-dir fails loudly instead of passing.

    A typo'd directory produced ``checked=0, ok=True`` before the guard —
    silent false success on a tool whose one job is verification.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    code = corpus.main(["live-diff", "--corpus-dir", str(empty),
                        "--ip", "203.0.113.5", "--dry-run"])
    assert code == 2
    assert "discovered nothing" in capsys.readouterr().err


def test_cli_credentials_half_filled_inventory_falls_back(tmp_path, monkeypatch):
    """An inventory node without user/password falls through to env, never "None".

    ``str(None)`` credentials would attempt real BMC logins as user "None"
    and risk an account lockout.
    """
    inventory = tmp_path / "inv.yaml"
    inventory.write_text("nodes:\n  - bmc:\n      ip: 10.0.0.9\n")
    monkeypatch.setenv("REDFISH_USERNAME", "opuser")
    monkeypatch.setenv("REDFISH_PASSWORD", "opsecret")
    assert corpus._bmc_credentials("10.0.0.9", inventory) == ("opuser", "opsecret")
    for var in ("REDFISH_USERNAME", "REDFISH_PASSWORD",
                "IDRAC_USERNAME", "IDRAC_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        corpus._bmc_credentials("10.0.0.9", inventory)
    assert excinfo.value.code == 2


@pytest.mark.parametrize("row", corpus.load_manifest(),
                         ids=lambda r: f"{r['vendor']}-{r['model']}")
def test_every_committed_corpus_self_checks_clean(row):
    """Each real vendor corpus self-diffs with zero drift through the engine.

    One test per manifest row: Dell, HPE, Supermicro X10/GB300, and NVIDIA all
    walk through the SAME discovery code — the vendor-genericity contract for
    the corpus-as-sim surface. Skipped per-corpus when the LFS tarball is not
    pulled (CI without ``git lfs pull`` stays green).
    """
    tarball = corpus._tarball_path(row)
    if not tarball.exists() or corpus._is_lfs_pointer(tarball):
        pytest.skip(f"{row['tarball']} not pulled (bare LFS pointer)")
    fetch = corpus_diff.corpus_fetcher(extract_corpus(tarball, row["arcname"]))
    report = corpus_diff.compare(fetch, fetch)
    summary = report["summary"]
    assert summary["ok"], [r for r in report["rows"] if r["status"] == "drift"]
    assert summary["checked"] > 0, "self-check walked nothing — discovery broke"
