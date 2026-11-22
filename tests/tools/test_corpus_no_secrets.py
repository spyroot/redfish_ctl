"""Guard: no committed corpus tarball may carry a real credential value.

A full Redfish crawl can include vendor OEM credential material — Dell exposes
salted password hashes, per-user IPMI keys, SNMPv3 keys, and SNMP community
strings as flat dotted attribute keys (``Users.2.SHA256Password`` etc.) inside a
DellAttributes object. A naive "is the ``Password`` field null" scan misses those,
so this test scans every committed corpus for any key whose last dotted segment is
a known credential suffix (:data:`tools.redact_corpus.SECRET_SUFFIXES`) and fails if
the value is a non-empty non-placeholder string. It is the regression guard for the
Dell credential redaction. A tarball that is still a bare LFS pointer is skipped.
"""
from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from tools import corpus
from tools.redact_corpus import SECRET_SUFFIXES

REPO_ROOT = Path(__file__).resolve().parents[2]
_PLACEHOLDERS = {"REDACTED", "", "null", "0", "0.0.0.0"}


def _real_secret(key: str, value) -> bool:
    """True if key is a credential suffix and value is a real (non-placeholder) string."""
    if not isinstance(value, str):
        return False
    last = key.lower().rsplit(".", 1)[-1]
    if last not in SECRET_SUFFIXES:
        return False
    return value.strip() and value.strip() not in _PLACEHOLDERS


def _scan(obj, hits: list[str], where: str) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _real_secret(k, v):
                hits.append(f"{where}:{k}")  # key only — never the value
            _scan(v, hits, where)
    elif isinstance(obj, list):
        for item in obj:
            _scan(item, hits, where)


@pytest.mark.parametrize(
    "row", corpus.load_manifest(), ids=lambda r: f"{r['vendor']}-{r['model']}"
)
def test_corpus_has_no_real_credentials(row):
    """Every JSON in the committed corpus tarball is free of real credential values."""
    import json

    path = REPO_ROOT / row["tarball"]
    if corpus._is_lfs_pointer(path):
        pytest.skip(f"{row['tarball']} is a bare LFS pointer; run `git lfs pull`")
    hits: list[str] = []
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            if not member.name.endswith(".json"):
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            try:
                data = json.loads(fh.read().decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                continue
            _scan(data, hits, Path(member.name).name)
    assert not hits, (
        f"{row['tarball']} carries {len(hits)} real credential value(s) "
        f"(keys, values withheld): {sorted(set(hits))[:10]}"
    )
