"""Integrity checks for the preserved Supermicro GB300 raw crawl corpus.

The active mock overlay remains ``tests/supermicro_fixtures/``. This test pins
the larger copied crawl under ``tests/supermicro_gb300_corpus/`` so the lab data
does not silently disappear as the GB300 environment ages out.
"""

import json
from pathlib import Path

CORPUS_ROOT = Path(__file__).parent / "supermicro_gb300_corpus" / "json_responses"
# One snapshot per crawled host. The redundant ``orig/`` backup tree is dropped
# (gitignored, never committed) so the corpus is a single de-duplicated full dump.
EXPECTED_HOST_DIRS = {
    "172.25.230.37",
    "192.168.254.120",
    "192.168.254.119",
    "10.43.3.209",
}


def _under_orig(path: Path) -> bool:
    """True if ``path`` lives under the ignored ``orig/`` backup tree.

    A local tool may re-materialize ``orig/`` on disk; it is gitignored and not
    part of the committed corpus, so the integrity checks ignore it.
    """
    return "orig" in path.relative_to(CORPUS_ROOT).parts


def test_gb300_raw_corpus_shape_is_preserved():
    """The de-duplicated raw crawl keeps every file and host snapshot."""
    files = [
        path for path in CORPUS_ROOT.rglob("*")
        if path.is_file() and not _under_orig(path)
    ]
    json_files = [path for path in files if path.suffix == ".json"]
    host_dirs = {
        str(path.relative_to(CORPUS_ROOT))
        for path in CORPUS_ROOT.rglob("*")
        if path.is_dir() and not _under_orig(path) and any(path.glob("*.json"))
    }

    assert len(files) == 2023
    assert len(json_files) == 2018
    assert host_dirs == EXPECTED_HOST_DIRS


def test_gb300_raw_corpus_json_artifacts_parse():
    """Every copied JSON artifact remains syntactically valid."""
    bad = []
    for path in CORPUS_ROOT.rglob("*.json"):
        if _under_orig(path):
            continue
        try:
            json.loads(path.read_text())
        except ValueError as exc:
            bad.append(f"{path.relative_to(CORPUS_ROOT)}: {exc}")

    assert bad == []
