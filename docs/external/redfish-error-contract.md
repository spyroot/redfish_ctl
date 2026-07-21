# Redfish Error Contract

Redfish error handling is a protocol contract, not a local formatting choice.
`redfish_ctl` must follow the DMTF Redfish protocol and schema model when a
service returns an error, including for unsupported operations.

## Binding Rule

Every HTTP error response that is a Redfish error must be normalized from the
service payload and schema/registry evidence. Do not replace it with a
hand-built string, a vendor-only shortcut, or a generic unsupported marker.

The normalized error keeps:

- HTTP status code.
- Top-level `error.code` and `error.message`.
- Every `error.@Message.ExtendedInfo` entry.
- `MessageId`, `Message`, `MessageArgs`, `MessageSeverity` or `Severity`, and
  `Resolution` when present.
- The error/resource schema descriptor when known, including `Schema`,
  `PublicationUri`, and local BMC `Uri`.
- The message registry descriptor when known, including `Registry`,
  `PublicationUri`, and local BMC `Uri`.
- The original target URI and raw error body when the body cannot be classified.

String rendering is only a human-readable projection of this object. It must
never be the authoritative data path for JSON, YAML, tests, or downstream
consumers.

## DMTF Anchors

The contract follows:

- DSP0266, the Redfish protocol specification, which defines the Redfish error
  response envelope and HTTP status behavior.
- DSP0268, the Redfish data model specification, which binds Redfish payloads to
  schemas through `@odata.type` and schema-defined properties.
- DSP8010, the Redfish schema bundle, which publishes JSON schema artifacts such
  as `Resource`, `Message`, `RedfishError`, and `redfish-error`.
- DSP8011, the Redfish registry bundle, which publishes message registries such
  as `Base`.

The service-local schema descriptor and registry descriptor are part of the
contract. A descriptor such as `/redfish/v1/JsonSchemas/Resource` or
`/redfish/v1/JsonSchemas/redfish-error` must keep its DMTF `PublicationUri`,
local `Uri`, and `Schema` identity.

The pinned local release contract is
`spec/dmtf/redfish/2026.1/manifest.yaml`. It names every DMTF 2026.1 artifact
that the implementation, tests, simulator, and automation rules use. Agents and
contributors must update that manifest and its gate tests when adding a new
schema bundle, registry bundle, mockup bundle, profile bundle, or simulator
corpus source.

Agent-readable protocol summaries live next to the manifest:

- `spec/dmtf/redfish/2026.1/reference/http-status-and-error-contract.md`
  summarizes response-code and error-envelope handling from the DMTF protocol
  documents.
- `spec/dmtf/redfish/2026.1/reference/telemetry-contract.md` summarizes the
  DMTF telemetry resource, registry, mockup, and exporter boundary.
- `spec/dmtf/redfish/2026.1/reference/output-adapter-plan.md` records the
  planned kubectl-style output adapter contract for human, JSON, and YAML
  rendering.

Canonical DMTF publication surfaces include:

| Surface | URL |
| --- | --- |
| DSP2043 mockups bundle, Redfish 2026.1 | `https://www.dmtf.org/sites/default/files/standards/documents/DSP2043_2026.1.zip` |
| DSP8010 schema bundle baseline, Redfish 2025.4 | `https://www.dmtf.org/sites/default/files/standards/documents/DSP8010_2025.4.zip` |
| DSP8010 schema bundle latest checked baseline, Redfish 2026.1 | `https://www.dmtf.org/sites/default/files/standards/documents/DSP8010_2026.1.zip` |
| DSP8011 standard registries bundle, Redfish 2026.1 | `https://www.dmtf.org/sites/default/files/standards/documents/DSP8011_2026.1.zip` |
| Schema index | `https://redfish.dmtf.org/redfish/schema_index` |
| JSON schema directory | `https://redfish.dmtf.org/schemas/v1/` |
| CSDL/XML schema example | `https://redfish.dmtf.org/schemas/v1/AccelerationFunction_v1.xml` |
| JSON schema example | `https://redfish.dmtf.org/schemas/v1/AccelerationFunction.v1_0_5.json` |
| YAML schema example | `https://redfish.dmtf.org/schemas/v1/AccelerationFunction.yaml` |
| Standard registries directory | `https://redfish.dmtf.org/registries/` |

DSP8010 is the baseline bundle class for schema validation. A release pin such
as `DSP8010_2025.4.zip` or `DSP8010_2026.1.zip` defines the reference schema
set for that Redfish release, but service payloads can still point at older
schema versions. The implementation must accept the service-observed
`PublicationUri`/`Schema` version and normalize it against the pinned bundle
instead of assuming one global latest version.

Implementations must not assume every DMTF pointer is the same file shape. A
service can expose JSON-schema descriptors, versioned JSON schema files,
unversioned JSON schema files, CSDL/XML schema files, or registry JSON files.
The parser keeps the publication URL and local service URL, then normalizes the
error data without crashing on version or format differences.

The DMTF schema index presents primary schemas by logical schema name and, when
available, links multiple representations such as `[csdl]`, `[json-schema]`,
and `[yaml]`. `redfish_ctl` must bind to the logical schema identity and
preserve the publication URI it observed; it must not assume a single canonical
extension or a single latest version.

`DSP2043` has a different role from `DSP8010`: it is a public DMTF mockup
payload bundle for simulator seeding, not a schema authority. The simulator may
load mockup payloads from `DSP2043`, but protocol validation still binds those
payloads to schemas and registries through `DSP8010`, `DSP8011`, and the
service-observed Redfish descriptors.

`DSP8011` is the DMTF registry bundle. It carries the Base registry versions
observed in current corpora, including Dell iDRAC `Base.1.12.1` and
GB300/OpenBMC-era `Base.1.18.1`, plus newer Base versions and Telemetry
registries. It is required local input for offline `MessageId` template
expansion, registry membership checks, and telemetry message validation.

## Vendor And Version Shape

Vendor/version differences are expected and must be normalized, not flattened.

- Dell iDRAC can expose the error schema as `RedfishError.v1_0_1` and point to
  DMTF `RedfishError.v1_0_1.json`.
- GB300/OpenBMC-era systems can expose `redfish-error.v1_0_2` and point to DMTF
  `redfish-error.v1_0_2.json`.
- Dell iDRAC can expose unversioned schema descriptor publication links such as
  `http://redfish.dmtf.org/schemas/v1/Resource.json` and
  `http://redfish.dmtf.org/schemas/v1/Message.json`.
- GB300/OpenBMC-era systems can expose versioned descriptor publication links
  such as `http://redfish.dmtf.org/schemas/v1/Resource.v1_19_0.json` and
  `http://redfish.dmtf.org/schemas/v1/Message.v1_2_1.json`.
- Message registries can be DMTF `Base` or vendor/OEM registries. The parser
  must preserve the registry prefix/version from `MessageId` and registry
  descriptors when available.
- `Message` can be missing or `null`; a valid message can still be represented
  by `MessageId` plus `MessageArgs`.

Known corpus-backed examples:

| Vendor/model class | Descriptor | DMTF publication URL |
| --- | --- | --- |
| Dell iDRAC | `Resource` | `http://redfish.dmtf.org/schemas/v1/Resource.json` |
| Dell iDRAC | `RedfishError.v1_0_1` | `http://redfish.dmtf.org/schemas/v1/RedfishError.v1_0_1.json` |
| Dell iDRAC | `Message` | `http://redfish.dmtf.org/schemas/v1/Message.json` |
| Dell iDRAC | `BaseMessages` registry `Base.1.12.1` | `https://redfish.dmtf.org/registries/v1/Base.1.12.1.json` |
| GB300/OpenBMC-era | `Resource` | `http://redfish.dmtf.org/schemas/v1/Resource.v1_19_0.json` |
| GB300/OpenBMC-era | `redfish-error` | `http://redfish.dmtf.org/schemas/v1/redfish-error.v1_0_2.json` |
| GB300/OpenBMC-era | `Message` | `http://redfish.dmtf.org/schemas/v1/Message.v1_2_1.json` |
| GB300/OpenBMC-era | `Base` registry `Base.1.18.1` | `https://redfish.dmtf.org/registries/Base.1.18.1.json` |

Unknown vendor shapes must fail closed into an unclassified Redfish/raw error
object that preserves status, target, body, and parse exception. They must not
pretend to be a supported schema-backed result.

## Unsupported Operations

Unsupported is still protocol data. If a BMC returns `501`, `404`, `405`, `409`,
or another Redfish error for an unsupported operation, the command may add
operator hints such as `suggested_command`, but it must preserve the normalized
Redfish error envelope as the primary machine-readable value.

## JSON And YAML Output

`--json` and `--yaml` output must serialize the same normalized object. YAML is
not allowed to be a second rendering path that drops `@Message.ExtendedInfo`,
schema pointers, registry pointers, HTTP status, or target. Output must not leak
Python private fields or Python object tags.

The planned output adapter is `-o/--output human|json|yaml|name|wide`, with
`--machine` as a stable automation alias for JSON-only, colorless output.
Telemetry exporter `--output prometheus|signalfx|otlp` is a backend selector and
must remain scoped to that subcommand or be renamed during the adapter
transition.

## Telemetry

Telemetry follows the same Redfish-first rule. `TelemetryService`,
`MetricDefinition`, `MetricReport`, `MetricReportDefinition`, `TelemetryData`,
`EventService`, `EventDestination`, metric resource schemas, and Telemetry
message registries are DMTF surfaces. Local Prometheus, SignalFx, and OTLP
exporters render normalized Redfish telemetry samples; they do not define the
protocol contract.

## Gate

`tests/test_redfish_error_contract.py` is the hard gate for this contract. It
pins:

- The DMTF 2026.1 release manifest under `spec/dmtf/redfish/2026.1/`.
- The local `DSP8010_2026.1.zip` schema bundle central-directory members.
- The local `DSP8011_2026.1.zip` registry bundle central-directory members.
- The local `DSP2043_2026.1.zip` mockup bundle central-directory members.
- The local telemetry WIP bundle central-directory members.
- DMTF schema and registry pointers in the Dell and GB300 full corpora.
- Safe parsing and string rendering of DMTF error shapes.
- JSON/YAML round-trip preservation of normalized error data.
- The output adapter plan that keeps human, JSON, and YAML on one normalized
  object.
- A static guard against command code that builds HTTP error payloads without
  using the Redfish error parser.
