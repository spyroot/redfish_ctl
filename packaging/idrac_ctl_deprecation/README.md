# idrac_ctl — renamed to redfish_ctl

**`idrac_ctl` has been renamed to [`redfish_ctl`](https://pypi.org/project/redfish-ctl/).**

This package is now a thin redirect: installing it just installs `redfish_ctl`, which still provides
the `idrac_ctl` command, `import idrac_ctl`, and the `IDRAC_*` environment variables as a
backward-compatible alias. It ships no code of its own.

## Install the new package directly

```bash
pip install redfish_ctl
```

- **New name:** `redfish_ctl` — a vendor-neutral Redfish/BMC CLI (Dell iDRAC, Supermicro incl.
  GB300/X10, HPE iLO, and generic DMTF Redfish).
- **Source & docs:** https://github.com/spyroot/redfish_ctl
- After installing `redfish_ctl`, the old `idrac_ctl` command, `import idrac_ctl`, and `IDRAC_*`
  env vars all keep working.
