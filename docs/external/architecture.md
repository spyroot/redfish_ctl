# Architecture

Author: Mus <spyroot@gmail.com>

`RedfishManager`, defined in `redfish_ctl/redfish_manager.py`, is the DMTF/shared command root.
`IDracManager`, defined in `redfish_ctl/idrac_manager.py`, is the Dell command root. Use
`redfish_ctl --vendor dell system` when tracing a Dell system command from the CLI to the HTTP client.

```text
CLI (`redfish_main.py`, argparse)
  -> selected manager's MRO-composed command registry
  -> command modules (`cmd_*.py`, `<domain>/cmd_*.py`)
  -> `RedfishManager` for DMTF commands and product-neutral HTTP
  -> vendor manager for vendor commands and response semantics
  -> requests over Redfish HTTPS
```

## Main Pieces

- `RedfishManager`, defined in `redfish_ctl/redfish_manager.py`, owns connection settings, HTTP verbs,
  Redfish response parsing, and the `CommandResult(data, discovered, extra, error)` return shape. It
  never imports vendor packages.
- `IDracManager` adds Dell/iDRAC defaults, OEM helpers, and Lifecycle Controller job handling. Its
  request/response overrides preserve Dell job identifiers returned in either `Location` or JSON.
- Shared DMTF commands subclass `RedfishManager`; Dell commands subclass `IDracManager`; other vendor
  commands subclass their vendor manager. Commands self-register with an `ApiRequestType` and `name=`
  through `__init_subclass__`.

## Vendor-Neutral Reads

The clearest cross-vendor command is `sensors`, defined in `redfish_ctl/sensors/cmd_sensors.py`. It
walks ServiceRoot -> Chassis -> Sensors by `@odata.id` links and returns sensor names, readings,
units, types, and health. `tests/test_sensors.py` runs it through the Supermicro fixture overlay, so
the test uses the real request path against a non-Dell tree.

The discovery pieces live in two places. `redfish_ctl/discover/classifier.py` classifies a ServiceRoot
as `dell`, `hpe`, `supermicro`, or `generic` using OEM keys, `@odata.type`, and manufacturer text.
`redfish_ctl/discovery/cmd_discovery.py` is the CLI command that recursively walks Redfish resources,
dumps the responses, and records allowed methods.

## Vendors

`redfish_ctl/vendors/<name>/` holds capability profiles. The Dell command code still lives mostly in
`IDracManager` and `redfish_ctl/delloem/`; moving that code into `vendors/dell/` is planned, not done.

Current vendor maturity is summarized in [Vendors](vendors.md). Short version:

- Dell iDRAC: the primary target, with query-parameter and JobService capability flags in
  `redfish_ctl/vendors/dell/capabilities.py`.
- Supermicro: read/query validated against a live GB300 BMC with Redfish 1.17.0, backed by
  `tests/supermicro_fixtures/` and the vendor-aware mock factory.
- HPE iLO: read/query validated against `tests/hpe_fixtures/` and the opt-in emulator canary in
  `examples/hpe_ilo_canary.sh`.
- Generic Redfish: conservative DMTF-style fallback backed by `tests/generic_fixtures/`.

The generic core never imports vendor packages.

## Host-System Selection

Some hosts expose more than one ComputerSystem. A Supermicro GB300 can expose the host as `System_0`
and the NVIDIA HGX baseboard as `HGX_Baseboard_0`. `RedfishManager.discover_computer_system_ids()`,
`discover_manager_ids()`, and `_host_system()` prefer the member with `Bios` or `Boot` links so host
commands route to the host system instead of a baseboard.

## Sync And Async

Most CLI commands call the synchronous request helpers. The async helpers (`api_async_get`,
`api_async_post`, `api_async_patch`, and `api_async_delete`) already exist for callers that need an
event loop. A future fleet proxy would build on those helpers; the proxy itself is not implemented.

## Known Structural Debt

- `IDracManager` is large; shared transport and retry behavior belongs in `RedfishManager`, while
  Dell Lifecycle Controller job semantics remain in `IDracManager`.
- Power, boot, and BIOS control paths need library-callable APIs, not only the CLI
  argument path, so a future proxy and other callers can reuse them directly.
- `firmware-update` exists as a guarded SimpleUpdate path. It requires a dry-run/confirm safety model;
  rollback and repository-management flows are still future work.
