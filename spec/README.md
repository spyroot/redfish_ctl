# Specification Bundles

This directory stores public DMTF Redfish reference bundles used by tests and
the simulator. The machine-readable release contract is
`dmtf/redfish/2026.1/manifest.yaml`.

## Layout

| Path | Role |
| --- | --- |
| `dmtf/redfish/2026.1/manifest.yaml` | Release contract that names DMTF artifacts, local bundle policy, and gates. |
| `dmtf/redfish/2026.1/protocol/` | Redfish protocol specification PDFs such as DSP0266. |
| `dmtf/redfish/2026.1/data-model/` | Redfish data model specification PDFs such as DSP0268. |
| `dmtf/redfish/2026.1/schemas/` | DSP8010 schema bundle baseline for JSON schema, CSDL/XML, YAML/OpenAPI, and guide checks. |
| `dmtf/redfish/2026.1/registries/` | DSP8011 standard registry bundle, including Base and Telemetry message registries. |
| `dmtf/redfish/2026.1/mockups/` | DSP2043 mockups bundle for simulator seed data. |
| `dmtf/redfish/2026.1/profiles/` | Redfish interoperability profile specification and bundle. |
| `dmtf/redfish/2026.1/guides/` | Message registry and other guide PDFs that are not only consumed from DSP8010/DSP8011. |
| `dmtf/redfish/2026.1/reference/` | Agent-readable summaries of protocol status, error, telemetry, and output contracts. |
| `dmtf/redfish/wip/` | Public DMTF work-in-progress material used only when the manifest names it. |
| `dmtf/redfish/extensions/` | Public Redfish extension specifications outside the core release. |

`DSP8010` and PDF/registry/profile/WIP archives follow the repository LFS rules.
`DSP2043` is intentionally stored as a normal binary Git object because the
checked-in bundle is small enough and should stay easy to fetch in ordinary
clones for simulator smoke tests.

Do not add ad-hoc root-level `DSP*.zip` or `DSP*.pdf` copies under this
directory. Add new public DMTF material under the release, WIP, or extension
path that matches its source, then update the release manifest and its tests.
