"""Offline tests for the DMTF spec fetcher (tools/fetch_dmtf_specs.py).

The fetcher downloads DMTF Redfish standards named in the baseline manifest;
downloading is a network step never exercised here. These tests cover the
offline logic: manifest loading, artifact selection, sha256 verification
semantics, and the list/print-binding reports against a synthetic manifest, so
the verification contract cannot regress without a network run.

Author Mus spyroot@gmail.com
"""
import hashlib
import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "fetch_dmtf_specs", REPO_ROOT / "tools" / "fetch_dmtf_specs.py")
fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch)


def _manifest(tmp_path, sha=None):
    """Write a synthetic two-artifact manifest and one on-disk artifact file.

    :param tmp_path: test temp dir (serves as the manifest's base directory).
    :param sha: optional pinned sha for DSPTEST in a binding block.
    :return: (manifest_path, the DSPTEST file path, its true sha256).
    """
    (tmp_path / "sub").mkdir()
    art = tmp_path / "sub" / "DSPTEST.zip"
    art.write_bytes(b"payload")
    true_sha = hashlib.sha256(b"payload").hexdigest()
    binding = f"\nbinding:\n  sha256:\n    DSPTEST: \"{sha}\"\n" if sha else ""
    text = (
        "artifacts:\n"
        "  - id: DSPTEST\n"
        "    url: \"https://example.test/DSPTEST.zip\"\n"
        "    localPath: \"sub/DSPTEST.zip\"\n"
        "  - id: DSPMISS\n"
        "    url: \"https://example.test/DSPMISS.pdf\"\n"
        "    localPath: \"sub/DSPMISS.pdf\"\n"
        + binding)
    mpath = tmp_path / "manifest.yaml"
    mpath.write_text(text)
    return mpath, art, true_sha


def test_missing_manifest_exits_2(tmp_path):
    """A missing manifest is a usage/environment error (exit 2)."""
    with pytest.raises(SystemExit) as e:
        fetch.load_manifest(tmp_path / "nope.yaml")
    assert e.value.code == 2


def test_select_unknown_id_exits_2(tmp_path):
    """Selecting an id absent from the manifest exits 2 with the known list."""
    mpath, _, _ = _manifest(tmp_path)
    arts = fetch._artifacts(fetch.load_manifest(mpath))
    with pytest.raises(SystemExit) as e:
        fetch._select(arts, "DSPNOPE")
    assert e.value.code == 2


def test_present_pinned_matching_is_a_noop(tmp_path, capsys):
    """A present file whose sha matches the pin is reported present, exit 0."""
    mpath, art, true_sha = _manifest(tmp_path, sha=hashlib.sha256(b"payload").hexdigest())
    code = fetch.cmd_fetch(fetch.load_manifest(mpath), mpath, "DSPTEST", force=False)
    assert code == 0
    assert "present  DSPTEST" in capsys.readouterr().out


def test_present_pinned_mismatch_fails(tmp_path):
    """A present file whose sha != the pin fails (exit 1), never a silent pass."""
    mpath, art, _ = _manifest(tmp_path, sha="0" * 64)
    code = fetch.cmd_fetch(fetch.load_manifest(mpath), mpath, "DSPTEST", force=False)
    assert code == 1


def test_list_reports_present_pinned_verified(tmp_path, capsys):
    """--list shows presence, pin, and verification per artifact."""
    mpath, art, true_sha = _manifest(tmp_path, sha=hashlib.sha256(b"payload").hexdigest())
    fetch.cmd_list(fetch.load_manifest(mpath), mpath)
    out = capsys.readouterr().out
    assert "DSPTEST" in out and "DSPMISS" in out
    dsptest = next(line for line in out.splitlines() if line.startswith("DSPTEST"))
    assert "yes" in dsptest and "ok" in dsptest       # present, pinned, verified ok


def test_print_binding_emits_present_shas(tmp_path, capsys):
    """--print-binding emits a sha row only for artifacts present on disk."""
    mpath, art, true_sha = _manifest(tmp_path)
    fetch.cmd_print_binding(fetch.load_manifest(mpath), mpath)
    out = capsys.readouterr().out
    assert f'DSPTEST: "{true_sha}"' in out
    assert "DSPMISS" not in out


def test_real_manifest_declares_the_core_standards():
    """The committed 2026.1 manifest declares the schema/mockup/protocol core.

    Guards the fetcher's data source: adding a standard is a manifest row, so
    the core artifacts the tool and the sim depend on must stay declared.
    """
    manifest = fetch.load_manifest(fetch.DEFAULT_MANIFEST)
    ids = {a["id"] for a in fetch._artifacts(manifest)}
    assert {"DSP8010", "DSP2043", "DSP0266", "DSP8011"} <= ids
