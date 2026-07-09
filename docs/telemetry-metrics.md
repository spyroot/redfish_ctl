# GB300 Telemetry Metrics

Author: Mus <spyroot@gmail.com>

This reference is generated from the Supermicro GB300 fixture files under
`tests/supermicro_fixtures/`; those fixtures are captured Redfish JSON used by
the offline tests, so this page does not require a live BMC or private endpoint.

## How To Read This

`MetricReports`, the Redfish collection represented by the fixture files named
`_redfish_v1_TelemetryService_MetricReports*.json`, carries the observed rows.
`MetricReportDefinitions`, the Redfish collection represented by the fixture
files named `_redfish_v1_TelemetryService_MetricReportDefinitions*.json`,
carries the source metric templates.

`MetricValue`, the value field in each Redfish `MetricReport` row, is a string
in this corpus. The observed type column below is inferred from those fixture
strings. Units are shown only when the exporter declares one, or as a name
hint when the Redfish report omits a unit. Treat `name hint` entries as
operator guidance, not schema-declared units.

`Context` is the parent Redfish fragment or path segment that disambiguates
repeated source metric names without copying the full fixture path.

`redfish_ctl exporter`, defined in `redfish_ctl/telemetry/cmd_exporter.py`, emits
numeric `MetricValue` rows only. Known fabric counters use curated
`hw.fabric.*` names; every other numeric row becomes a generated
`hw.gb300.*` metric name derived from the source metric name.

## Safe Consumption

Start with direct read-only Redfish GET paths before running a long-lived
exporter process:

```bash
redfish_ctl metric-definitions
redfish_ctl metric-reports --report HGX_ProcessorPortMetrics_0
```

Then run a one-shot Prometheus render from `redfish_ctl exporter`, which reads
the BMC and prints text instead of opening a listener:

```bash
redfish_ctl exporter --vendor supermicro --once --output prometheus
```

For SignalFx, `SPLUNK_ACCESS_TOKEN`, the ingest token read by the exporter
from the process environment, and `SPLUNK_INGEST_URL`, the full
`/v2/datapoint` URL read by the exporter, are required only when pushing.
Use `--once --output signalfx` first to inspect the datapoint envelope
without posting externally.

## Checking Live Data In Splunk

SignalFx is Splunk Observability Cloud, so the exporter's `signalfx` output pushes
these metrics straight into Splunk Observability — no extra bridge.

1. Push to your org (realm ingest URL + an access token). `SPLUNK_INGEST_URL` and
   `SPLUNK_ACCESS_TOKEN` are read from the environment by `redfish_ctl exporter`:

```bash
export SPLUNK_ACCESS_TOKEN='<org access token>'
export SPLUNK_INGEST_URL='https://ingest.<realm>.signalfx.com/v2/datapoint'
redfish_ctl exporter --vendor supermicro --output signalfx --push-signalfx
```

2. Find the data in Splunk Observability. Under **Metrics -> Metric Finder**, search the
   metric names this exporter emits: `hw.fabric.*` (NVLink/port link state, BER, RX/TX
   throughput and errors), `hw.gb300.*` (GPU plus general chassis sensors), and
   `hw.temperature`, `hw.energy_kwh`, `hw.component_integrity.enabled`. Every datapoint
   carries these **dimensions** for filtering/grouping: `host`, `chassis`, `gpu`, `port`,
   `sensor`, `vendor`, `report`.

3. Confirm points are arriving with a chart or SignalFlow query, e.g. fabric receive rate
   per port on one host:

```
data('hw.fabric.raw_rx_gbps', filter=filter('host', '<bmc-host>')).publish()
```

Datapoints land within a few seconds of the push; when the Metric Finder shows the `hw.*`
names carrying your `host`/`vendor` dimensions, live data is flowing.

For **Splunk Enterprise/Cloud (HEC)** rather than Observability: run the Prometheus
listener (`redfish_ctl exporter --output prometheus`, no `--once`) and point a Splunk
OpenTelemetry Collector (prometheus receiver -> `splunk_hec` exporter) at it, which lands
the same metrics in a HEC index.

For a **native OTLP** pipeline (no Collector hop for the BMC data), use
`redfish_ctl exporter --output otlp` — it pushes these same `hw.*` series over OTLP and honors the
standard `OTEL_EXPORTER_OTLP_*` environment so it behaves like any other OTLP producer. The identity
dimensions become OTel resource attributes and the per-metric dimensions become datapoint attributes;
monotonic counters are emitted as OTLP Sum and instantaneous readings as Gauge. Needs the
`redfish_ctl[otlp]` extra. See [Telemetry exporter](telemetry-exporter.md#otlp-opentelemetry).

> Live verification of the push needs a real `SPLUNK_ACCESS_TOKEN` and your realm's
> `SPLUNK_INGEST_URL`; without them, use `--once --output signalfx` to validate the
> datapoint envelope offline.

## Report Inventory

| Report | Definition type | Definition metrics | Observed rows | Observed value types |
|---|---:|---:|---:|---|
| `HGX_CpuProcessorMetrics_0` | OnRequest | 11 | 184 | boolean:2, number:154, string:28 |
| `HGX_HealthMetrics_0` | OnRequest | 6 | 14 | string:14 |
| `HGX_MemoryMetrics_0` | OnRequest | 13 | 52 | boolean:4, number:48 |
| `HGX_PlatformEnvironmentMetrics_0` | OnRequest | 19 | 48 | number:48 |
| `HGX_ProcessorGPMMetrics_0` | OnRequest | 22 | 144 | number:144 |
| `HGX_ProcessorMetrics_0` | OnRequest | 177 | 704 | boolean:584, number:88, string:32 |
| `HGX_ProcessorPortGPMMetrics_0` | OnRequest | 4 | 288 | number:288 |
| `HGX_ProcessorPortMetrics_0` | OnRequest | 32 | 2308 | number:2236, string:72 |
| `HGX_ProcessorResetMetrics_0` | OnRequest | 8 | 32 | number:28, string:4 |
| `PlatformEnvironmentMetrics_0` | OnRequest | 8 | 50 | number:50 |

## Metric Catalog

Rows are grouped by Redfish report. `Expanded rows` is the count of concrete
fixture `MetricValue` rows matched by the definition template. `0` means the
definition exists but the current fixture did not include a matching sample.

### `HGX_CpuProcessorMetrics_0`

| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |
|---|---|---|---:|---:|---|
| `ProcessorModule_{ProcessorId}_CPU_{CpuId}_CoreUtil_{CoreId}` | Chassis/HGX_CPU_{CpuId}/Sensors | not declared | number:144 | 144 | `hw.gb300.processor_module_processor_id_cpu_cpu_id_core_util_core_id` |
| `ProcessorModule_{ProcessorId}_MemCntl_0_Freq_0` | Chassis/HGX_CPU_{CpuId}/Sensors | not declared | number:2 | 2 | `hw.gb300.processor_module_processor_id_mem_cntl_0_freq_0` |
| `ProcessorModule_{ProcessorId}_CPU_0_CpuFreq_0` | Chassis/HGX_CPU_{CpuId}/Sensors | not declared | number:2 | 2 | `hw.gb300.processor_module_processor_id_cpu_0_cpu_freq_0` |
| `ProcessorModule_{ProcessorId}_Vreg_0_CpuVoltage_0` | Chassis/HGX_CPU_{CpuId}/Sensors | name hint: voltage | number:2 | 2 | `hw.gb300.processor_module_processor_id_vreg_0_cpu_voltage_0` |
| `ProcessorModule_{ProcessorId}_Vreg_0_SocVoltage_0` | Chassis/HGX_CPU_{CpuId}/Sensors | name hint: voltage | number:2 | 2 | `hw.gb300.processor_module_processor_id_vreg_0_soc_voltage_0` |
| `MemoryPageRetirementCount` | Oem/Nvidia | name hint: count | number:2 | 2 | `hw.gb300.memory_page_retirement_count` |
| `EDPViolationState` | Oem/Nvidia | not declared | string:2 | 2 | `not exported by exporter` |
| `PowerBreakPerformanceState` | Oem/Nvidia | name hint: power | string:2 | 2 | `not exported by exporter` |
| `MemorySpareChannelPresence` | Oem/Nvidia | not declared | boolean:2 | 2 | `not exported by exporter` |
| `State` | Status | not declared | string:20 | 20 | `not exported by exporter` |
| `State` | Status | not declared | string:4 | 4 | `not exported by exporter` |

### `HGX_HealthMetrics_0`

| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |
|---|---|---|---:|---:|---|
| `Health` | Status | not declared | string:2 | 2 | `not exported by exporter` |
| `HealthRollup` | Status | not declared | string:2 | 2 | `not exported by exporter` |
| `Health` | Status | not declared | string:4 | 4 | `not exported by exporter` |
| `HealthRollup` | Status | not declared | string:4 | 4 | `not exported by exporter` |
| `Health` | Status | not declared | string:1 | 1 | `not exported by exporter` |
| `HealthRollup` | Status | not declared | string:1 | 1 | `not exported by exporter` |

### `HGX_MemoryMetrics_0`

| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |
|---|---|---|---:|---:|---|
| `RowRemappingFailed` | Oem/Nvidia | not declared | boolean:4 | 4 | `not exported by exporter` |
| `OperatingSpeedMHz` | OperatingSpeedMHz | name hint: MHz | number:4 | 4 | `hw.gb300.operating_speed_mhz` |
| `BandwidthPercent` | BandwidthPercent | name hint: percent | number:4 | 4 | `hw.gb300.bandwidth_percent` |
| `CapacityUtilizationPercent` | CapacityUtilizationPercent | name hint: percent | number:4 | 4 | `hw.gb300.capacity_utilization_percent` |
| `CorrectableECCErrorCount` | LifeTime | name hint: count | number:4 | 4 | `hw.gb300.correctable_eccerror_count` |
| `UncorrectableECCErrorCount` | LifeTime | name hint: count | number:4 | 4 | `hw.gb300.uncorrectable_eccerror_count` |
| `CorrectableRowRemappingCount` | Oem/Nvidia/RowRemapping | name hint: count | number:4 | 4 | `hw.gb300.correctable_row_remapping_count` |
| `UncorrectableRowRemappingCount` | Oem/Nvidia/RowRemapping | name hint: count | number:4 | 4 | `hw.gb300.uncorrectable_row_remapping_count` |
| `MaxAvailabilityBankCount` | Oem/Nvidia/RowRemapping | name hint: count | number:4 | 4 | `hw.gb300.max_availability_bank_count` |
| `HighAvailabilityBankCount` | Oem/Nvidia/RowRemapping | name hint: count | number:4 | 4 | `hw.gb300.high_availability_bank_count` |
| `PartialAvailabilityBankCount` | Oem/Nvidia/RowRemapping | name hint: count | number:4 | 4 | `hw.gb300.partial_availability_bank_count` |
| `LowAvailabilityBankCount` | Oem/Nvidia/RowRemapping | name hint: count | number:4 | 4 | `hw.gb300.low_availability_bank_count` |
| `NoAvailabilityBankCount` | Oem/Nvidia/RowRemapping | name hint: count | number:4 | 4 | `hw.gb300.no_availability_bank_count` |

### `HGX_PlatformEnvironmentMetrics_0`

| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |
|---|---|---|---:|---:|---|
| `HGX_BMC_0_Temp_0` | Chassis/HGX_BMC_0/Sensors | name hint: temperature | number:1 | 1 | `hw.gb300.hgx_bmc_0_temp_0` |
| `{BSWild}` | Chassis/HGX_Chassis_0/Sensors | varies by resolved sensor | number:1 | 1 | `hw.gb300.<resolved_metric_property>` |
| `ProcessorModule_{PMWild}_CPU_0_Energy_0` | Chassis/HGX_CPU_{PMWild}/Sensors | name hint: energy | number:2 | 2 | `hw.gb300.processor_module_pmwild_cpu_0_energy_0` |
| `ProcessorModule_{PMWild}_CPU_0_EnforcedEDPc_0` | Chassis/HGX_CPU_{PMWild}/Sensors | not declared | number:2 | 2 | `hw.gb300.processor_module_pmwild_cpu_0_enforced_edpc_0` |
| `ProcessorModule_{PMWild}_CPU_0_EnforcedEDPp_0` | Chassis/HGX_CPU_{PMWild}/Sensors | not declared | number:2 | 2 | `hw.gb300.processor_module_pmwild_cpu_0_enforced_edpp_0` |
| `ProcessorModule_{PMWild}_CPU_0_Power_0` | Chassis/HGX_CPU_{PMWild}/Sensors | name hint: power | number:2 | 2 | `hw.gb300.processor_module_pmwild_cpu_0_power_0` |
| `ProcessorModule_{PMWild}_CPU_0_TempAvg_0` | Chassis/HGX_CPU_{PMWild}/Sensors | name hint: temperature | number:2 | 2 | `hw.gb300.processor_module_pmwild_cpu_0_temp_avg_0` |
| `ProcessorModule_{PMWild}_CPU_0_TempLimit_0` | Chassis/HGX_CPU_{PMWild}/Sensors | name hint: temperature | number:2 | 2 | `hw.gb300.processor_module_pmwild_cpu_0_temp_limit_0` |
| `ProcessorModule_{PMWild}_Vreg_0_CpuPower_0` | Chassis/HGX_CPU_{PMWild}/Sensors | name hint: power | number:2 | 2 | `hw.gb300.processor_module_pmwild_vreg_0_cpu_power_0` |
| `ProcessorModule_{PMWild}_Vreg_0_SocPower_0` | Chassis/HGX_CPU_{PMWild}/Sensors | name hint: power | number:2 | 2 | `hw.gb300.processor_module_pmwild_vreg_0_soc_power_0` |
| `HGX_GPU_{GWild}_DRAM_0_Power_0` | Chassis/HGX_GPU_{GWild}/Sensors | name hint: power | number:4 | 4 | `hw.gb300.hgx_gpu_gwild_dram_0_power_0` |
| `HGX_GPU_{GWild}_DRAM_0_Temp_0` | Chassis/HGX_GPU_{GWild}/Sensors | name hint: temperature | number:4 | 4 | `hw.gb300.hgx_gpu_gwild_dram_0_temp_0` |
| `HGX_GPU_{GWild}_Energy_0` | Chassis/HGX_GPU_{GWild}/Sensors | name hint: energy | number:4 | 4 | `hw.gb300.hgx_gpu_gwild_energy_0` |
| `HGX_GPU_{GWild}_Power_0` | Chassis/HGX_GPU_{GWild}/Sensors | name hint: power | number:8 | 8 | `hw.gb300.hgx_gpu_gwild_power_0` |
| `HGX_GPU_{GWild}_TEMP_0` | Chassis/HGX_GPU_{GWild}/Sensors | name hint: temperature | number:4 | 4 | `hw.gb300.hgx_gpu_gwild_temp_0` |
| `HGX_GPU_{GWild}_TEMP_1` | Chassis/HGX_GPU_{GWild}/Sensors | name hint: temperature | number:4 | 4 | `hw.gb300.hgx_gpu_gwild_temp_1` |
| `HGX_ProcessorModule_{PMWild}_Exhaust_Temp_0` | Chassis/HGX_ProcessorModule_{PMWild}/Sensors | name hint: temperature | number:2 | 2 | `hw.gb300.hgx_processor_module_pmwild_exhaust_temp_0` |
| `HGX_ProcessorModule_{PMWild}_Inlet_Temp_0` | Chassis/HGX_ProcessorModule_{PMWild}/Sensors | name hint: temperature | number:2 | 2 | `hw.gb300.hgx_processor_module_pmwild_inlet_temp_0` |
| `HGX_ProcessorModule_{PMWild}_Inlet_Temp_1` | Chassis/HGX_ProcessorModule_{PMWild}/Sensors | name hint: temperature | number:2 | 2 | `hw.gb300.hgx_processor_module_pmwild_inlet_temp_1` |

### `HGX_ProcessorGPMMetrics_0`

| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |
|---|---|---|---:|---:|---|
| `TensorCoreActivityPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.tensor_core_activity_percent` |
| `SMOccupancyPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.smoccupancy_percent` |
| `SMActivityPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.smactivity_percent` |
| `PCIeRawTxBandwidthGbps` | Oem/Nvidia | Gbps | number:4 | 4 | `hw.gb300.pcie_raw_tx_bandwidth_gbps` |
| `PCIeRawRxBandwidthGbps` | Oem/Nvidia | Gbps | number:4 | 4 | `hw.gb300.pcie_raw_rx_bandwidth_gbps` |
| `NVOfaUtilizationPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.nvofa_utilization_percent` |
| `NVLinkRawTxBandwidthGbps` | Oem/Nvidia | Gbps | number:4 | 4 | `hw.fabric.raw_tx_gbps` |
| `NVLinkRawRxBandwidthGbps` | Oem/Nvidia | Gbps | number:4 | 4 | `hw.fabric.raw_rx_gbps` |
| `NVLinkDataTxBandwidthGbps` | Oem/Nvidia | Gbps | number:4 | 4 | `hw.fabric.tx_gbps` |
| `NVLinkDataRxBandwidthGbps` | Oem/Nvidia | Gbps | number:4 | 4 | `hw.fabric.rx_gbps` |
| `NVJpgUtilizationPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.nvjpg_utilization_percent` |
| `{InstanceId}` | Oem/Nvidia/NVJpgInstanceUtilizationPercent | varies by resolved sensor | number:32 | 32 | `hw.gb300.<resolved_metric_property>` |
| `{InstanceId}` | Oem/Nvidia/NVDecInstanceUtilizationPercent | varies by resolved sensor | number:32 | 32 | `hw.gb300.<resolved_metric_property>` |
| `NVDecUtilizationPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.nvdec_utilization_percent` |
| `IntegerActivityUtilizationPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.integer_activity_utilization_percent` |
| `IMMAUtilizationPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.immautilization_percent` |
| `HMMAUtilizationPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.hmmautilization_percent` |
| `GraphicsEngineActivityPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.graphics_engine_activity_percent` |
| `FP64ActivityPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.fp64_activity_percent` |
| `FP32ActivityPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.fp32_activity_percent` |
| `FP16ActivityPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.fp16_activity_percent` |
| `DMMAUtilizationPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.dmmautilization_percent` |

### `HGX_ProcessorMetrics_0`

| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |
|---|---|---|---:|---:|---|
| `State` | Status | not declared | not observed | 0 | `not exported by exporter` |
| `PCIeType` | PCIeInterface | not declared | string:4 | 4 | `not exported by exporter` |
| `MaxLanes` | PCIeInterface | not declared | number:4 | 4 | `hw.gb300.max_lanes` |
| `LanesInUse` | PCIeInterface | not declared | number:4 | 4 | `hw.gb300.lanes_in_use` |
| `OperatingSpeedMHz` | OperatingSpeedMHz | name hint: MHz | number:4 | 4 | `hw.gb300.operating_speed_mhz` |
| `BandwidthPercent` | BandwidthPercent | name hint: percent | number:4 | 4 | `hw.gb300.bandwidth_percent` |
| `SMUtilizationPercent` | Oem/Nvidia | name hint: percent | number:4 | 4 | `hw.gb300.smutilization_percent` |
| `CorrectableECCErrorCount` | CacheMetricsTotal/LifeTime | name hint: count | number:4 | 4 | `hw.gb300.correctable_eccerror_count` |
| `UncorrectableECCErrorCount` | CacheMetricsTotal/LifeTime | name hint: count | number:4 | 4 | `hw.gb300.uncorrectable_eccerror_count` |
| `CorrectableErrorCount` | PCIeErrors | name hint: count | number:4 | 4 | `hw.gb300.correctable_error_count` |
| `NonFatalErrorCount` | PCIeErrors | name hint: count | number:4 | 4 | `hw.gb300.non_fatal_error_count` |
| `FatalErrorCount` | PCIeErrors | name hint: count | number:4 | 4 | `hw.gb300.fatal_error_count` |
| `L0ToRecoveryCount` | PCIeErrors | name hint: count | number:4 | 4 | `hw.gb300.l0_to_recovery_count` |
| `ReplayCount` | PCIeErrors | name hint: count | number:4 | 4 | `hw.gb300.replay_count` |
| `ReplayRolloverCount` | PCIeErrors | name hint: count | number:4 | 4 | `hw.gb300.replay_rollover_count` |
| `NAKSentCount` | PCIeErrors | name hint: count | number:4 | 4 | `hw.gb300.naksent_count` |
| `NAKReceivedCount` | PCIeErrors | name hint: count | number:4 | 4 | `hw.gb300.nakreceived_count` |
| `ThrottleReasons` | Oem/Nvidia | not declared | not observed | 0 | `not exported by exporter` |
| `PCIeTXBytes` | Oem/Nvidia | name hint: bytes | number:4 | 4 | `hw.gb300.pcie_txbytes` |
| `PCIeRXBytes` | Oem/Nvidia | name hint: bytes | number:4 | 4 | `hw.gb300.pcie_rxbytes` |
| `PowerLimitThrottleDuration` | PowerLimitThrottleDuration | name hint: power | string:4 | 4 | `not exported by exporter` |
| `ThermalLimitThrottleDuration` | ThermalLimitThrottleDuration | not declared | string:4 | 4 | `not exported by exporter` |
| `HardwareViolationThrottleDuration` | Oem/Nvidia | not declared | string:4 | 4 | `not exported by exporter` |
| `GlobalSoftwareViolationThrottleDuration` | Oem/Nvidia | not declared | string:4 | 4 | `not exported by exporter` |
| `AccumulatedGPUContextUtilizationDuration` | Oem/Nvidia | not declared | string:4 | 4 | `not exported by exporter` |
| `AccumulatedSMUtilizationDuration` | Oem/Nvidia | not declared | string:4 | 4 | `not exported by exporter` |
| `RampDownWattsPerSecond` | RampDownWattsPerSecond | not declared | number:4 | 4 | `hw.gb300.ramp_down_watts_per_second` |
| `RampDownHysteresisSeconds` | RampDownHysteresisSeconds | not declared | number:4 | 4 | `hw.gb300.ramp_down_hysteresis_seconds` |
| `RampUpWattsPerSecond` | RampUpWattsPerSecond | not declared | number:4 | 4 | `hw.gb300.ramp_up_watts_per_second` |
| `TMPFloorPercent` | TMPFloorPercent | name hint: percent | number:4 | 4 | `hw.gb300.tmpfloor_percent` |
| `ImmediateRampDown` | ImmediateRampDown | not declared | boolean:4 | 4 | `not exported by exporter` |
| `RemainingLifetimeCircuitryPercent` | RemainingLifetimeCircuitryPercent | name hint: percent | number:4 | 4 | `hw.gb300.remaining_lifetime_circuitry_percent` |
| `Enabled` | Enabled | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/BAR0Firewall | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/BAR0Firewall | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/BAR0Firewall | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/EGMMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/EGMMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/EGMMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/BAR0TypeConfig | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/BAR0TypeConfig | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/BAR0TypeConfig | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/CCDevMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/CCDevMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/CCDevMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/CCMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/CCMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/CCMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/ClockLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/ClockLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/ClockLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/ECCEnable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/ECCEnable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/ECCEnable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/EDPpScalingFactor | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/EDPpScalingFactor | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/EDPpScalingFactor | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/ForceTestCoupling | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/ForceTestCoupling | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/ForceTestCoupling | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/FusingMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/FusingMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/FusingMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/HBMFrequencyChange | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/HBMFrequencyChange | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/HBMFrequencyChange | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/HULKLicenseUpdate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/HULKLicenseUpdate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/HULKLicenseUpdate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/InSystemTest | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/InSystemTest | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/InSystemTest | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/InfoROMFileSystemRecreate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/InfoROMFileSystemRecreate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/InfoROMFileSystemRecreate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/NVLinkDisable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/NVLinkDisable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/NVLinkDisable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/PCIeVFConfiguration | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/PCIeVFConfiguration | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/PCIeVFConfiguration | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/PowerSmoothingPrivilegeLevel1 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/PowerSmoothingPrivilegeLevel1 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/PowerSmoothingPrivilegeLevel1 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/PowerSmoothingPrivilegeLevel2 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/PowerSmoothingPrivilegeLevel2 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/PowerSmoothingPrivilegeLevel2 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/RowRemappingAllowed | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/RowRemappingAllowed | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/RowRemappingAllowed | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/RowRemappingFeature | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/RowRemappingFeature | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/RowRemappingFeature | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/TGPCurrentLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/TGPCurrentLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/TGPCurrentLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/TGPMaxLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/TGPMaxLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/TGPMaxLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/TGPMinLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/TGPMinLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/TGPMinLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/InbandReconfigPermissions/TGPRatedLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/InbandReconfigPermissions/TGPRatedLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/InbandReconfigPermissions/TGPRatedLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/BAR0Firewall | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/BAR0Firewall | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/BAR0Firewall | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/EGMMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/EGMMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/EGMMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/BAR0TypeConfig | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/BAR0TypeConfig | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/BAR0TypeConfig | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/CCDevMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/CCDevMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/CCDevMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/CCMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/CCMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/CCMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/ClockLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/ClockLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/ClockLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/ECCEnable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/ECCEnable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/ECCEnable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/EDPpScalingFactor | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/EDPpScalingFactor | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/EDPpScalingFactor | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/ForceTestCoupling | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/ForceTestCoupling | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/ForceTestCoupling | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/FusingMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/FusingMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/FusingMode | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/HBMFrequencyChange | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/HBMFrequencyChange | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/HBMFrequencyChange | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/HULKLicenseUpdate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/HULKLicenseUpdate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/HULKLicenseUpdate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/InSystemTest | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/InSystemTest | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/InSystemTest | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/InfoROMFileSystemRecreate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/InfoROMFileSystemRecreate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/InfoROMFileSystemRecreate | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/NVLinkDisable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/NVLinkDisable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/NVLinkDisable | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/PCIeVFConfiguration | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/PCIeVFConfiguration | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/PCIeVFConfiguration | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/PowerSmoothingPrivilegeLevel1 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/PowerSmoothingPrivilegeLevel1 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/PowerSmoothingPrivilegeLevel1 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/PowerSmoothingPrivilegeLevel2 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/PowerSmoothingPrivilegeLevel2 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/PowerSmoothingPrivilegeLevel2 | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/RowRemappingAllowed | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/RowRemappingAllowed | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/RowRemappingAllowed | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/RowRemappingFeature | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/RowRemappingFeature | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/RowRemappingFeature | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/TGPCurrentLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/TGPCurrentLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/TGPCurrentLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/TGPMaxLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/TGPMaxLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/TGPMaxLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/TGPMinLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/TGPMinLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/TGPMinLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowFLRPersistentConfig` | Nvidia/DOEReconfigPermissions/TGPRatedLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowOneShotConfig` | Nvidia/DOEReconfigPermissions/TGPRatedLimit | not declared | boolean:4 | 4 | `not exported by exporter` |
| `AllowPersistentConfig` | Nvidia/DOEReconfigPermissions/TGPRatedLimit | not declared | boolean:4 | 4 | `not exported by exporter` |

### `HGX_ProcessorPortGPMMetrics_0`

| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |
|---|---|---|---:|---:|---|
| `NVLinkDataTxBandwidthGbps` | Oem/Nvidia | Gbps | number:72 | 72 | `hw.fabric.tx_gbps` |
| `NVLinkDataRxBandwidthGbps` | Oem/Nvidia | Gbps | number:72 | 72 | `hw.fabric.rx_gbps` |
| `NVLinkRawTxBandwidthGbps` | Oem/Nvidia | Gbps | number:72 | 72 | `hw.fabric.raw_tx_gbps` |
| `NVLinkRawRxBandwidthGbps` | Oem/Nvidia | Gbps | number:72 | 72 | `hw.fabric.raw_rx_gbps` |

### `HGX_ProcessorPortMetrics_0`

| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |
|---|---|---|---:|---:|---|
| `CurrentSpeedGbps` | CurrentSpeedGbps | Gbps | number:72 | 72 | `hw.fabric.port_speed` |
| `TXBytes` | TXBytes | By | number:72 | 72 | `hw.fabric.tx_bytes` |
| `RXBytes` | RXBytes | By | number:72 | 72 | `hw.fabric.rx_bytes` |
| `RXErrors` | RXErrors | name hint: count | number:72 | 72 | `hw.fabric.rx_errors` |
| `RXFrames` | Networking | name hint: count | number:72 | 72 | `hw.fabric.rx_frames` |
| `TXFrames` | Networking | name hint: count | number:72 | 72 | `hw.fabric.tx_frames` |
| `TXDiscards` | Networking | not declared | number:72 | 72 | `hw.fabric.tx_discards` |
| `MalformedPackets` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.fabric.malformed_packets` |
| `VL15Dropped` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.fabric.vl15_dropped` |
| `VL15TXPackets` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.fabric.vl15_tx_packets` |
| `VL15TXBytes` | Oem/Nvidia | By | number:72 | 72 | `hw.fabric.vl15_tx_bytes` |
| `NeighborMTUDiscards` | Oem/Nvidia | not declared | number:72 | 72 | `hw.gb300.neighbor_mtudiscards` |
| `LinkErrorRecoveryCount` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.fabric.link_error_recovery_count` |
| `LinkDownedCount` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.fabric.link_down_count` |
| `RXRemotePhysicalErrors` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.fabric.rx_remote_physical_errors` |
| `RXSwitchRelayErrors` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.fabric.rx_switch_relay_errors` |
| `QP1Dropped` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.gb300.qp1_dropped` |
| `TXWait` | Oem/Nvidia | not declared | number:72 | 72 | `hw.fabric.tx_wait` |
| `BitErrorRate` | Oem/Nvidia | not declared | number:72 | 72 | `hw.fabric.bit_error_rate` |
| `TXNoProtocolBytes` | Oem/Nvidia | By | number:72 | 72 | `hw.fabric.tx_no_protocol_bytes` |
| `RXNoProtocolBytes` | Oem/Nvidia | By | number:72 | 72 | `hw.fabric.rx_no_protocol_bytes` |
| `RuntimeError` | Oem/Nvidia/NVLinkErrors | name hint: count | number:72 | 72 | `hw.gb300.runtime_error` |
| `TrainingError` | Oem/Nvidia/NVLinkErrors | name hint: count | number:72 | 72 | `hw.gb300.training_error` |
| `LinkDownReasonCode` | Oem/Nvidia | not declared | string:72 | 72 | `not exported by exporter` |
| `EffectiveBER` | Oem/Nvidia | not declared | number:72 | 72 | `hw.fabric.effective_ber` |
| `SymbolErrors` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.fabric.symbol_errors` |
| `TotalRawBER` | Oem/Nvidia | not declared | number:72 | 72 | `hw.fabric.raw_ber` |
| `IntentionalLinkDownCount` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.fabric.intentional_link_down_count` |
| `UnintentionalLinkDownCount` | Oem/Nvidia | name hint: count | number:72 | 72 | `hw.fabric.unintentional_link_down_count` |
| `RXWidth` | Oem/Nvidia | not declared | number:72 | 72 | `hw.gb300.rxwidth` |
| `TXWidth` | Oem/Nvidia | not declared | number:72 | 72 | `hw.gb300.txwidth` |
| `CurrentSpeedGbps` | CurrentSpeedGbps | Gbps | number:4 | 4 | `hw.fabric.port_speed` |

### `HGX_ProcessorResetMetrics_0`

| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |
|---|---|---|---:|---:|---|
| `PF_FLR_ResetEntryCount` | PF_FLR_ResetEntryCount | name hint: count | number:4 | 4 | `hw.gb300.pf_flr_reset_entry_count` |
| `PF_FLR_ResetExitCount` | PF_FLR_ResetExitCount | name hint: count | number:4 | 4 | `hw.gb300.pf_flr_reset_exit_count` |
| `ConventionalResetEntryCount` | ConventionalResetEntryCount | name hint: count | number:4 | 4 | `hw.gb300.conventional_reset_entry_count` |
| `ConventionalResetExitCount` | ConventionalResetExitCount | name hint: count | number:4 | 4 | `hw.gb300.conventional_reset_exit_count` |
| `FundamentalResetEntryCount` | FundamentalResetEntryCount | name hint: count | number:4 | 4 | `hw.gb300.fundamental_reset_entry_count` |
| `FundamentalResetExitCount` | FundamentalResetExitCount | name hint: count | number:4 | 4 | `hw.gb300.fundamental_reset_exit_count` |
| `IRoTResetExitCount` | IRoTResetExitCount | name hint: count | number:4 | 4 | `hw.gb300.iro_treset_exit_count` |
| `LastResetType` | LastResetType | not declared | string:4 | 4 | `not exported by exporter` |

### `PlatformEnvironmentMetrics_0`

| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |
|---|---|---|---:|---:|---|
| `BMC_0_DCSCM_Temp_0` | Chassis/BMC_0/Sensors | name hint: temperature | number:1 | 1 | `hw.gb300.bmc_0_dcscm_temp_0` |
| `{BSWild}` | Chassis/HGX_Chassis_0/Sensors | varies by resolved sensor | not observed | 0 | `not exported by exporter` |
| `IO_Board_{IWild}_CX7_0_Temp_0` | Chassis/IO_Board_{IWild}/Sensors | name hint: temperature | not observed | 0 | `not exported by exporter` |
| `IO_Board_{IWild}_CX7_1_Temp_0` | Chassis/IO_Board_{IWild}/Sensors | name hint: temperature | not observed | 0 | `not exported by exporter` |
| `NVME_M2_0_Temp_0` | Chassis/NVME_M2_0/Sensors | name hint: temperature | number:1 | 1 | `hw.gb300.nvme_m2_0_temp_0` |
| `{PDBWild}` | Chassis/PDB_0/Sensors | varies by resolved sensor | number:13 | 13 | `hw.gb300.<resolved_metric_property>` |
| `{BFSWild}` | Chassis/Riser_Slot{BFWild}_BlueField_3_SmartNIC_Main_Card/Sensors | varies by resolved sensor | not observed | 0 | `not exported by exporter` |
| `StorageBackplane_{SBWild}_SSD_{SBDWild}_Temp_0` | Chassis/StorageBackplane_{SBWild}/Sensors | name hint: temperature | number:8 | 8 | `hw.gb300.storage_backplane_sbwild_ssd_sbdwild_temp_0` |
