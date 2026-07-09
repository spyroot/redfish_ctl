"""The packaged version and the CLI `--version` must not drift apart.

Regression guard for a real mismatch: the wheel was built as 1.1.0 (from setup.py)
while `--version` reported 1.0.14 (from redfish_ctl/version.py). setup.py now reads
version.py, so these tests fail loudly if anyone re-hardcodes or the two diverge.
"""
import re
import subprocess
import sys
from pathlib import Path

from redfish_ctl import version as version_mod
from redfish_ctl.redfish_main import __version__ as cli_version

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_cli_version_matches_version_module():
    """`--version` (redfish_main.__version__) uses the canonical version.py value."""
    assert cli_version == version_mod.__version__


def test_version_is_pep440_ish():
    """The single version string is a sane dotted release, not a placeholder."""
    assert re.fullmatch(r"\d+\.\d+\.\d+([abrc.].*)?", version_mod.__version__)


def test_setup_py_version_matches_version_module():
    """The wheel/sdist version (setup.py --version) equals version.py.

    Runs setup.py in a subprocess so it exercises the real read-from-version.py
    path that names the built artifact — the exact thing that drifted before.
    """
    out = subprocess.run(
        [sys.executable, "setup.py", "--version"],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == version_mod.__version__
