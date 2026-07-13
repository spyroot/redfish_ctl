# Vendors

Author: Mus <spyroot@gmail.com>

Vendor facts live in capability profiles so the Redfish core can stay product-neutral. Dell,
Supermicro, HPE, and generic Redfish now all have fixture-backed coverage, but they do not have the
same control surface.

## Support Matrix

**Dell iDRAC.** Proof lives in `tests/dell_fixtures/`, live-test patterns, and Dell OEM command
coverage. The common ids are `System.Embedded.1` and `iDRAC.Embedded.1`. Dell remains the primary
control target.

**Supermicro GB300.** Proof lives in `tests/supermicro_fixtures/`, captured from a read-only GB300
Redfish 1.17 crawl. The important ids are `System_0`, `HGX_Baseboard_0`, `BMC_0`, and `HGX_BMC_0`.
Read/query and telemetry are validated; job scheduling stays conservative.

**Supermicro X10 (early Redfish).** Validated live only — no committed fixture — so treat "Supermicro"
as a range, not one target. An X10SDV-TLN4F (Xeon-D, ASPEED AST2400, BMC 3.88, **Redfish 1.0.1**)
returns `403` on its Redfish service root and everything under it until the **SFT-DCMS** license is
activated (the raw hex key goes in the BMC web-UI license field, not the Redfish
`LicenseManager.ActivateLicense` action). Once licensed, `system`, `console-info`, `chassis`, and
`manager-time` work over Redfish, but this early 1.0.1 firmware is thin: `boot-state` returns empty
boot data and `UpdateService`/`FirmwareInventory` are a `403`/`404` stub, so there is no Redfish
firmware update. Flashing this board BMC 3.88 -> 4.00 (verified live) did **not** change that: it
stayed `RedfishVersion 1.0.1` with `UpdateService` still `403` and `FirmwareInventory` still `404`, so
the X10SDV has no Redfish update path on any BMC firmware — use the BMC web UI or Supermicro SUM. The
fuller `UpdateService`/`FirmwareInventory` in Supermicro's *Redfish Reference Guide 2.0a* applies to
newer platforms (X11/X12/H12+), not the X10.

**HPE iLO.** Proof lives in `tests/hpe_fixtures/`, imported from the HPE iLO emulator corpus, plus
the live-emulator canary in `examples/hpe_ilo_canary.sh`. The common ids are `Systems/1`,
`Managers/1`, and `Chassis/1`. Read/query, Secure Boot, logs, network, telemetry, and guarded
dry-run paths are proven; HPE OEM write flows are not a target yet.

**Generic DMTF.** Proof lives in `tests/generic_fixtures/`, a DMTF-style rackmount tree. The common
ids are `Systems/437XR1138R2`, `Managers/BMC`, and `Chassis/1U`. This stays conservative and covers
standard Redfish reads.

The important point: non-Dell support is real for the vendor-neutral read paths, but Dell remains the
main target for deep lifecycle control and Dell OEM operations.

## Runtime Discovery

`classify_vendor()`, defined in `redfish_ctl/discover/classifier.py`, detects `dell`, `hpe`,
`supermicro`, or `generic` from a ServiceRoot dict. It checks OEM keys first, then an OEM-prefixed
`@odata.type`, then manufacturer/vendor text.

`get_vendor()`, defined in `redfish_ctl/vendors/__init__.py`, returns the matching capability profile
or the generic fallback:

```python
from redfish_ctl.vendors import get_vendor

caps = get_vendor("dell")
if caps.one_query_param_per_uri:
    ...
```

## Cross-Vendor Reads

The cleanest shared commands are link-following Redfish readers:

```bash
redfish_ctl system
redfish_ctl chassis
redfish_ctl sensors
redfish_ctl network-adapters
redfish_ctl network-ports
redfish_ctl ethernet-interfaces
redfish_ctl metric-reports
redfish_ctl component-integrity
redfish_ctl secure-boot
redfish_ctl logs
redfish_ctl oem-info
```

These commands are valuable because they start from advertised Redfish links instead of assuming Dell
resource ids. On multi-system hosts, `CommandBase` also picks the host ComputerSystem by looking for
`Bios` or `Boot` links. That keeps a GB300 host (`System_0`) distinct from the HGX baseboard member.

## Adding A Vendor

Start small and prove the read path first:

1. Add `redfish_ctl/vendors/<name>/capabilities.py` with only facts you have verified.
2. Add a fixture overlay under `tests/<vendor>_fixtures/`; include upstream license files when the
   corpus requires them.
3. Use `redfish_mock_factory("<vendor>")`, defined in `tests/conftest.py`, so tests exercise the same
   request path as the CLI.
4. Cover ServiceRoot classification, host/manager id discovery, sensors, inventory, network, logs, or
   telemetry before adding vendor-specific writes.
5. Add a live or emulator canary only when it is read-only or explicitly guarded.

The first useful vendor does not need command parity with Dell. It needs honest capability flags,
fixtures, and proof that standard Redfish reads work.

## Migration

Dell command code still lives mostly in `redfish_ctl/base_manager.py` and `redfish_ctl/delloem/`.
Moving that code under `vendors/dell/` is planned as commands are split out.
