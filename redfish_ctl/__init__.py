"""redfish_ctl — the going-forward name for the idrac_ctl package (compat alias).

Importing ``redfish_ctl`` (and ``from redfish_ctl.<submodule> import ...``) resolves to
the ``idrac_ctl`` package during the rename, so new code can adopt the redfish_ctl name
today. ``idrac_ctl`` stays the canonical package until the git-mv phase, at which point
this shim flips direction. See .internal/REDFISH_CTL_RENAME_PLAN.md.
"""
import importlib
import sys

# Import the real package. This pulls in the whole command tree (each command registers
# via __init_subclass__), so every idrac_ctl submodule is now in sys.modules.
_real = importlib.import_module("idrac_ctl")

# Alias idrac_ctl and every already-imported submodule under the redfish_ctl name, using
# the SAME module objects. So `from redfish_ctl.redfish_manager import CommandResult`
# returns the identical class as `from idrac_ctl.redfish_manager import CommandResult`
# (no duplicate module copies), and `import redfish_ctl` is idrac_ctl.
for _name, _mod in list(sys.modules.items()):
    if _name == "idrac_ctl" or _name.startswith("idrac_ctl."):
        sys.modules["redfish_ctl" + _name[len("idrac_ctl"):]] = _mod
