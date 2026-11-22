"""Extract the DSP2043 DMTF mockups bundle to a cached temp dir for tests.

The bundle (``spec/dmtf/redfish/2026.1/mockups/DSP2043_2026.1.zip``, tracked by
Git LFS) stores one mockup profile per directory, each Redfish resource at
``<path>/index.json``. Tests call :func:`mockup_profile_dir` to extract the
zip once per process (mirroring ``tests/vendor_corpus.py``) and get back a
profile root the mock BMC server serves with ``--mockup-dir``.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
import zipfile
from pathlib import Path

_CACHE: dict[str, Path] = {}


def is_lfs_pointer(path: Path) -> bool:
    """Report whether ``path`` is a bare Git-LFS pointer (not yet pulled).

    :param path: file to probe.
    :return: True for a pointer stub, False for real content or a read error.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(120)
    except OSError:
        return False
    return head.startswith(b"version https://git-lfs.github.com/spec")


def _bundle_root(extracted: Path) -> Path:
    """Return the single top-level directory of an extracted bundle.

    :param extracted: temp dir the zip was extracted into.
    :return: the bundle's internal root (e.g. ``DSP2043_2026.1``).
    :raises ValueError: when the zip did not extract to exactly one root dir.
    """
    roots = [entry for entry in extracted.iterdir() if entry.is_dir()]
    if len(roots) != 1:
        raise ValueError(
            f"expected one bundle root under {extracted}, found {len(roots)}"
        )
    return roots[0]


def mockup_profile_dir(bundle: Path, profile: str) -> Path:
    """Extract ``bundle`` once (cached) and return one profile's directory.

    :param bundle: path to the DSP2043 ``.zip`` mockups bundle.
    :param profile: profile directory name (e.g. ``public-telemetry``).
    :return: the extracted ``<bundle-root>/<profile>`` directory.
    """
    key = str(bundle)
    if key not in _CACHE:
        tmp = Path(tempfile.mkdtemp(prefix="dsp2043_mockup_"))
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(tmp)
        _CACHE[key] = _bundle_root(tmp)
    return _CACHE[key] / profile
