"""Extract a committed vendor corpus tarball to a cached temp dir for tests.

The vendor corpora ship as one LFS-tracked ``.tar.gz`` per vendor (see
``tools/pack_corpus.py``) so the repository carries a single file per corpus
instead of thousands of loose JSON fixtures. Tests call :func:`corpus_dir` to
extract a tarball once (cached for the process) and get back a directory the
mock BMC server can serve with ``--corpus-dir``.
"""
from __future__ import annotations

import atexit
import shutil
import tarfile
import tempfile
from pathlib import Path

_CACHE: dict[str, Path] = {}


def corpus_dir(tarball: Path, leaf: str) -> Path:
    """Extract ``tarball`` once (cached) and return its ``leaf`` corpus directory."""
    key = str(tarball)
    if key not in _CACHE:
        tmp = Path(tempfile.mkdtemp(prefix="redfish_corpus_"))
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        with tarfile.open(tarball) as tar:
            try:
                tar.extractall(tmp, filter="data")  # Python 3.12+ path-safe filter
            except TypeError:  # pragma: no cover - Python < 3.12 has no filter kwarg
                tar.extractall(tmp)
        _CACHE[key] = tmp
    return _CACHE[key] / leaf
