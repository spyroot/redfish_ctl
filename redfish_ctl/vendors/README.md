# Vendor packages

The generic Redfish core (`RedfishManager`) is product-neutral. Anything specific
to one server vendor lives here, in its own subdirectory, so vendor specifics stay
clearly separated and new vendors can be added incrementally without touching the
core.

```
vendors/
  base.py            # VendorCapabilities model + registry
  dell/              # Dell iDRAC
  hpe/               # HPE iLO (placeholder)
  lenovo/            # Lenovo XClarity Controller
  supermicro/        # Supermicro (placeholder)
```

## Convention for a vendor package

Each `vendors/<name>/` contains:

- `capabilities.py` — a `VendorCapabilities` profile registered via `register(...)`.
  It declares what the vendor's Redfish service supports (which query parameters,
  recurring-job scheduling, lifecycle events, OEM prefix) so commands can gate
  vendor-specific behavior cleanly.
- `cmd_*.py` — vendor-specific command modules (self-registering `CommandBase`
  subclasses), added incrementally.
- `README.md` — what's here and any vendor quirks.

Look up a profile:

```python
from redfish_ctl.vendors import get_vendor
caps = get_vendor("dell")           # unknown vendor -> conservative "generic" profile
if caps.job_scheduling:
    ...
```

## Migration note

Dell command code currently also lives in `redfish_ctl/base_manager.py` and
`redfish_ctl/delloem/`. It migrates into `vendors/dell/` incrementally, alongside the
planned `redfish_ctl` → `redfish_ctl` rename. The core must never import a vendor
package; vendor packages may depend on the core.
