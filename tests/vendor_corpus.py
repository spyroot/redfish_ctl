"""Materialize committed vendor corpora to cached temp dirs for tests."""
from __future__ import annotations

import atexit
import shutil
import tarfile
import tempfile
from pathlib import Path

from redfish_ctl import corpora

_CACHE: dict[str, Path] = {}

_LEGACY_TARBALL_IDS = {
    "dell_xr8620t_corpus.tar.gz": "dell-xr8620t",
    "hpe_dl360_corpus.tar.gz": "hpe-dl360",
    "nvidia_gb300_node2_corpus.tar.gz": "nvidia-gb300-node2",
    "supermicro_gb300_corpus.tar.gz": "supermicro-gb300",
    "supermicro_x10_corpus.tar.gz": "supermicro-x10sdv",
}


def corpus_dir(tarball: Path, leaf: str) -> Path:
    """Materialize a corpus once and return the directory served by mock BMC tests."""
    corpus_id = _LEGACY_TARBALL_IDS.get(tarball.name)
    if corpus_id:
        key = corpus_id
        if key not in _CACHE:
            tmp = Path(tempfile.mkdtemp(prefix="redfish_corpus_"))
            atexit.register(shutil.rmtree, tmp, ignore_errors=True)
            _CACHE[key] = corpora.materialize(tmp, corpus_id=corpus_id)[0]
        return _CACHE[key]

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
