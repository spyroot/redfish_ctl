# Generic Redfish test fixtures (DMTF, BSD-3-Clause)

A slice of DMTF's `public-rackmount1` reference mockup — a **product-neutral**
Redfish tree — used to prove redfish_ctl's commands work on standard Redfish, not
just the three real vendors we have (Dell/Supermicro/HPE). It uses yet another id
scheme (`Systems/437XR1138R2`), so it's an independent check that discovery and
the link-navigated commands make no vendor assumptions.

Served by the mock via `redfish_mock_factory("generic")`.

## Provenance

From DMTF's Redfish-Mockup-Server (`public-rackmount1`):
<https://github.com/DMTF/Redfish-Mockup-Server>. Copyright DMTF; BSD-3-Clause, full
text retained in `LICENSE.DMTF`. This is a redistributed slice produced with
`tests/tools/import_redfish_mockup.py` (bounded link walk).
