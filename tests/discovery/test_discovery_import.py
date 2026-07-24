"""Offline regression test: importing the discovery command must not import numpy.

The discovery command (idrac_ctl/discovery/cmd_discovery.py) only needs numpy for
``np.save`` inside ``save_url_file_mapping``. A top-level ``import numpy`` made the
whole module unimportable in environments without numpy, which blocked collecting
the offline pytest suite. The import is now lazy, so importing the module must not
pull numpy into ``sys.modules``.

Author Mus spyroot@gmail.com
"""
import importlib
import sys


def test_discovery_module_imports_without_numpy(monkeypatch):
    """Importing cmd_discovery fresh does not import numpy at module load time.

    Uses ``monkeypatch.delitem`` so both the discovery module and ``numpy`` are
    RESTORED on teardown. A bare ``sys.modules.pop("numpy")`` leaves numpy
    half-removed (its ``numpy.*`` submodules stay), so a later test that imports
    numpy reloads it — a warning on CPython, a ``RecursionError`` on some numpy
    builds (e.g. the conda env). Restoring keeps the rest of the suite clean.
    """
    module_name = "idrac_ctl.discovery.cmd_discovery"

    # Drop cached copies so the import re-executes the module body; delitem
    # records the originals and puts them back when the test finishes.
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.delitem(sys.modules, "numpy", raising=False)

    importlib.import_module(module_name)

    assert "numpy" not in sys.modules, (
        "importing idrac_ctl.discovery.cmd_discovery must not import numpy; "
        "the numpy import should be lazy inside save_url_file_mapping"
    )
