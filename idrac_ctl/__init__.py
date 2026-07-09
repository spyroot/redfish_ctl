"""idrac_ctl — backward-compatible alias for the redfish_ctl package.

The tool was renamed ``idrac_ctl`` -> ``redfish_ctl``. This shim keeps
``import idrac_ctl`` and ``from idrac_ctl.<submodule> import ...`` working (resolving
to the same objects as ``redfish_ctl``) for existing callers. New code should import
``redfish_ctl``. See .internal/REDFISH_CTL_RENAME_PLAN.md.
"""
import importlib
import sys

# Import the real package (registers the whole command tree via __init_subclass__),
# then alias it and every submodule under the legacy idrac_ctl name using the SAME
# module objects, so identities hold across both names.
_real = importlib.import_module("redfish_ctl")

for _name, _mod in list(sys.modules.items()):
    if _name == "redfish_ctl" or _name.startswith("redfish_ctl."):
        sys.modules["idrac_ctl" + _name[len("redfish_ctl"):]] = _mod
