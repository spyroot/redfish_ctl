# Telemetry Contract

Redfish telemetry in `redfish_ctl` has two layers:

- DMTF Redfish telemetry resources and message registries;
- local exporters and tracing that render normalized samples to Prometheus,
  SignalFx, or OTLP.

Export and tracing formats do not replace the Redfish protocol contract.

## Source Anchors

| Source | Local path | Contract surface |
| --- | --- | --- |
| DSP0268 Redfish Data Model Specification 2026.1 | `../data-model/DSP0268_2026.1.pdf` | `TelemetryService`, `MetricDefinition`, `MetricReport`, `MetricReportDefinition`, `TelemetryData`, `EventService`, and metric resource schemas. |
| DSP8010 Redfish Schema Bundle 2026.1 | `../schemas/DSP8010_2026.1.zip` | Telemetry JSON schema, CSDL/XML, and YAML/OpenAPI artifacts. |
| DSP8011 Redfish Standard Registries Bundle 2026.1 | `../registries/DSP8011_2026.1.zip` | `Telemetry.*` and `Base.*` message registries. |
| DSP2043 Redfish Mockups Bundle 2026.1 | `../mockups/DSP2043_2026.1.zip` | `public-telemetry` mockup and DSP2046 telemetry examples. |
| DSP-IS0027 WIP80 | `../../wip/telemetry-streaming/DSP-IS0027_WIP80.zip` | `TelemetryFeed` streaming proposal schemas and mockups. |

## DMTF Resource Surface

The current DMTF baseline names these telemetry-relevant resources:

- `TelemetryService`
- `MetricDefinition`
- `MetricReport`
- `MetricReportDefinition`
- `TelemetryData`
- `Triggers`
- `EventService`
- `EventDestination`
- `EnvironmentMetrics`
- `ThermalMetrics`
- `PowerSupplyMetrics`
- `ProcessorMetrics`
- `MemoryMetrics`
- `Sensor`

The `TelemetryService` action surface includes:

- `ClearMetricReports`
- `ClearTelemetryData`
- `CollectTelemetryData`
- `ResetMetricReportDefinitionsToDefaults`
- `ResetTriggersToDefaults`
- `SubmitTestMetricReport`

## Implementation Alignment

| Surface | Current expectation |
| --- | --- |
| Redfish read path | Samples are taken from Redfish resource payloads and keep resource identity, BMC identity, vendor, and metric dimensions. |
| Metric export path | Prometheus, SignalFx, and OTLP render normalized samples. Exporters must not mutate Redfish payload semantics. |
| OTLP resource attributes | BMC identity and host identity map to OTLP resource attributes. Per-sample dimensions map to datapoint attributes. |
| OTLP instruments | Counters and cumulative energy metrics become monotonic sums; other sampled values become gauges. |
| Tracing | Redfish client spans include method, target host, status code, exception data, and error status for `4xx`/`5xx`. |
| Async route | Context propagation must survive executor/async request boundaries. |
| Unsupported telemetry | Unsupported resources or actions return normalized Redfish error objects, not local strings. |

## Required Bundle Checks

The contract gate checks these bundle members by central directory:

- DSP8010 telemetry schemas: `TelemetryService`, `MetricDefinition`,
  `MetricReport`, `MetricReportDefinition`, `TelemetryData`, and `Sensor`;
- DSP8011 registries: `Telemetry.1.0.0`, `Telemetry.1.1.0`,
  `Telemetry.1.2.0`, plus Base registries used by live corpora;
- DSP2043 examples: `public-telemetry/TelemetryService`, `MetricReports`,
  `MetricReportDefinitions`, `MetricDefinitions`, `TelemetryData`, and
  DSP2046 telemetry request/response examples;
- DSP-IS0027 WIP: `TelemetryFeed_v1.xml`, `TelemetryService_v1.xml`, and
  public PDU telemetry feed mockups.

## Agent Rules

- Bind telemetry implementation to DMTF resources first, exporter schemas
  second.
- Do not add a telemetry metric name without a fixture or schema-backed source
  path.
- Do not treat an exporter `--output` backend as the CLI output renderer.
- Do not mark live telemetry smoke as green without status read-back, cleanup
  evidence, and the exact candidate commit.
