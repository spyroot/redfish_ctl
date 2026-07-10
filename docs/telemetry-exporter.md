# Telemetry Exporter

Author: Mus <spyroot@gmail.com>

`redfish_ctl exporter`, defined in `redfish_ctl/telemetry/cmd_exporter.py`, is the read-only path for
turning BMC Redfish telemetry into metrics. I use it when the BMC can see hardware state that an
in-band host agent misses: chassis power, fans, voltages, GPU power, and NVLink fabric counters.

## What It Reads

- Chassis, Processor, and Memory `EnvironmentMetrics` resources, discovered by the
  `environment-metrics` command, where many BMCs publish power and energy rollups.
- Chassis `ThermalSubsystem`, linked from Chassis resources, with `ThermalMetrics`
  temperature readings exposed as per-zone `hw.temperature` samples.
- Chassis `Sensors`, followed through linked Sensor resources.
- Chassis `LeakDetection` / `LeakDetectors`, followed from each chassis `ThermalSubsystem`.
- TelemetryService `MetricReports`, where GB300 exposes fabric and GPU metric properties.
- GPU `nvlink-ports`, `network-adapters`, and `component-integrity` command output.

The exporter emits `hw.power`, `hw.temperature`, `hw.fan_speed`, `hw.voltage`, `hw.energy_kwh`,
`hw.gpu.power`, `hw.leak.state`, and `hw.fabric.*`. EnvironmentMetrics rollups add `resource_type`
and `resource` dimensions so chassis, processor, and memory power can be separated. `hw.leak.state`,
derived from linked `LeakDetector` rows, is `0` for clear detector states and `1` for warning or
critical states. Fabric metrics include link state, negotiated speed, RX/TX bytes, bandwidth,
FEC/CRC-style counters when Redfish exposes them, and other NVLink error counters.

## Credentials

For exporter runs, keep BMC credentials in environment variables or a gitignored runtime file. Do not
put the password on argv; the exporter rejects `--idrac_password`.

`.internal/idrac_exporter.env`, created by the operator before runtime, is a simple `KEY=VALUE` file:

```bash
mkdir -p .internal
cat > .internal/idrac_exporter.env <<'EOF'
REDFISH_IP=192.0.2.29
REDFISH_USERNAME=admin
REDFISH_PASSWORD=replace-with-runtime-secret
REDFISH_PORT=443
EOF
```

## Prometheus

The default mode serves Prometheus text at `/metrics`:

```bash
redfish_ctl exporter \
  --credential-file .internal/idrac_exporter.env \
  --vendor supermicro \
  --listen 0.0.0.0 \
  --port 9109
```

For a local smoke read, render once and exit:

```bash
redfish_ctl exporter \
  --credential-file .internal/idrac_exporter.env \
  --vendor supermicro \
  --once \
  --output prometheus
```

## Labels

Every series carries the join labels used by the GB300 dashboards:

| Label | Value |
|---|---|
| `host.name` | `gb300-poc1-slotN` |
| `node` | `slotN` |
| `server.address` | `192.0.2.{40+N}` |
| `bmc.ip` | BMC address from `REDFISH_IP` or `--label-bmc-ip` |
| `vendor` | `supermicro`, `dell`, or the value passed with `--vendor` |

The default slot math is `N = BMC last octet - 20`. For BMC `192.0.2.29`, the exporter labels the
series as `host.name=gb300-poc1-slot9`, `node=slot9`, and `server.address=192.0.2.49`.

Use `--label-bmc-ip` only when the connection address is not the BMC address you want in the metric
labels.

ThermalSubsystem temperature samples set `source=thermal-subsystem` and include `chassis`, `sensor`,
and `zone` dimensions. The `zone` dimension comes from Redfish `PhysicalContext` when present, or
falls back to the reported sensor name.

## SignalFx

SignalFx push mode uses `SPLUNK_ACCESS_TOKEN`, the ingest token read from the process environment,
and `SPLUNK_INGEST_URL`, the ingest URL read from the process environment.

```bash
redfish_ctl exporter \
  --credential-file .internal/idrac_exporter.env \
  --vendor supermicro \
  --output signalfx \
  --push-signalfx
```

For tests and dry runs, use `--once --output signalfx`. That prints the SignalFx datapoint envelope
without posting anything.

## OTLP (OpenTelemetry)

`--output otlp` pushes the same `hw.*` series natively over OTLP, so `redfish_ctl` drops into an
existing OpenTelemetry pipeline as just another producer — no Prometheus/Collector hop needed. It
needs the OpenTelemetry SDK, shipped as an extra:

```bash
pip install "redfish_ctl[otlp]"
```

It honors the standard OTel environment variables (`OTEL_EXPORTER_OTLP_ENDPOINT`,
`OTEL_EXPORTER_OTLP_PROTOCOL` = `grpc` | `http/protobuf`, `OTEL_EXPORTER_OTLP_HEADERS`,
`OTEL_RESOURCE_ATTRIBUTES`), with `--otlp-endpoint` / `--otlp-protocol` overrides:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
redfish_ctl exporter --vendor supermicro --output otlp --interval 30      # push loop
redfish_ctl exporter --vendor supermicro --output otlp --once             # push once
```

The contract is unchanged: metric names and dimension keys are identical to the Prometheus/SignalFx
outputs. The identity dimensions (`host.name`, `server.address`, `bmc.ip`, `node`, `vendor`) map to
OTel **resource** attributes; the per-metric dimensions (`gpu`, `port`, `chassis`, `system`, `index`,
`resource_type`, `resource`) map to **datapoint** attributes. Monotonic cumulative counters (fabric
byte/frame/error/packet/count totals and `hw.energy_kwh`) are emitted as OTLP **Sum**; everything
instantaneous stays a **Gauge**.

## What Good Looks Like

A Prometheus scrape should include at least one chassis power metric and, on GB300, fabric metrics:

```text
hw.power{...} 1349.263802
hw.power{resource_type="Memory",resource="GPU_0_DRAM_0",...} 34.458
hw.gpu.power{gpu="GPU_0",...} 231.958
hw.leak.state{detector="Chassis_0_LeakDetector_0_ColdPlate",...} 0
hw.fabric.link_up{fabric="nvlink",gpu="GPU_0",port="NVLink_0",...} 1
hw.fabric.rx_bytes{fabric="nvlink",gpu="GPU_0",port="NVLink_0",...} 9460179851686
```

No live write is involved. If the command fails, check credentials, BMC reachability, and whether the
BMC exposes the modern telemetry resources listed at the top of this page.
