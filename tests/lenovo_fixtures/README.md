# Lenovo XCC test fixtures

A small synthetic Lenovo XClarity Controller (XCC) Redfish slice used to prove
that discovery and profile logic handle Lenovo's documented shape. It is not a
redistributed vendor mockup; values are reduced, documentation-seeded examples
based on the public Lenovo XCC REST API reference.

## Provenance

Facts pinned by this slice:

- `ServiceRoot.Vendor` is `Lenovo`, and `ServiceRoot.ProtocolFeaturesSupported`
  advertises `$select`, `$filter`, `only`, and `$expand` support.
- The primary ComputerSystem and Manager ids are `/redfish/v1/Systems/1` and
  `/redfish/v1/Managers/1`.
- BIOS pending settings are served at `/redfish/v1/Systems/1/Bios/Pending`.
- Lenovo BIOS attributes can be category-prefixed, such as `Memory_MemorySpeed`
  and `Processors_CStates`.
- UpdateService exposes `#UpdateService.SimpleUpdate` plus HTTP push URI
  fallbacks `/fwupdate` and `/mfwupdate`.

Reference: <https://pubs.lenovo.com/xcc-restapi/>
