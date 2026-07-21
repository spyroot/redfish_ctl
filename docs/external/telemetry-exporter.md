# Telemetry Exporter

Author: Mus <spyroot@gmail.com>

`redfish_ctl exporter`, defined in `redfish_ctl/telemetry/cmd_exporter.py`, is the read-only path for
turning BMC Redfish telemetry into metrics. Reach for it when the BMC can see hardware state that an
in-band host collector misses: chassis power, fans, voltages, GPU power, and NVLink fabric counters.

## Deployment Model: One Exporter Per BMC

The exporter follows one rule: **one exporter, one BMC, one metric stream.** A running exporter
polls a single BMC and publishes that BMC's metrics — nothing else. It never combines streams from
several servers; merging, routing, and fleet-wide views are the job of the tools built for that (an
OpenTelemetry Collector or Prometheus), which every telemetry pipeline already runs.

This keeps the exporter small and predictable. A slow or dead BMC affects only its own stream, so
one bad server never blocks the view of the rest. Monitoring more servers means starting more
copies — the code never changes, only the count. And when one stream goes quiet, there is no
ambiguity about which server it was.

### On Kubernetes

Two long-running pieces cooperate:

- The **controller** (under `k8s/controller/`) watches `RedfishEndpoint` resources — one per BMC —
  and keeps each resource's status (power state, health, temperature summary) up to date for
  anything in the cluster that reads Kubernetes objects.
- One **exporter pod per BMC** streams that BMC's metrics. Each pod either pushes OTLP to the
  cluster's OpenTelemetry Collector (the standard agent/gateway most clusters already run — set
  `OTEL_EXPORTER_OTLP_ENDPOINT` to its service address) or serves `/metrics` for Prometheus to
  scrape per pod. Credentials come from a per-BMC Secret, never from the image.

Adding server nineteen to the rack means adding one `RedfishEndpoint` and one exporter pod. The
scheduler spreads the pods, the Collector merges the streams, and nothing existing is touched.

### On Bare Metal

No cluster is needed: each exporter is just a process. systemd runs one instance per BMC from a
single template unit:

```ini
# /etc/systemd/system/redfish-exporter@.service
[Unit]
Description=Redfish telemetry exporter for BMC %i

[Service]
EnvironmentFile=/etc/redfish-exporter/%i.env
ExecStart=/usr/local/bin/redfish_ctl exporter --output otlp
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Each instance reads its own environment file (`/etc/redfish-exporter/bmc-01.env` and so on) holding
that BMC's `REDFISH_IP`, credentials, and the Collector endpoint. Eighteen BMCs are eighteen small
files and one command:

```bash
systemctl enable --now redfish-exporter@bmc-{01..18}
```

Rotating a credential means updating that BMC's file and restarting that one unit. For a combined
view, run an OpenTelemetry Collector (or Prometheus) on the same host or rack and point every
instance at it — the aggregation stays in the aggregator.

### Sizing

This model is sized for tens up to a couple hundred BMCs per site. Far beyond that, shard the
exporters across several hosts; do not look for a mode where one process owns the whole fleet —
that mode deliberately does not exist.

## Consumption Models

Different consumers use the same Redfish read paths under different latency and liveness
assumptions. Treat each mode separately when tuning or debugging.

| Consumer | Where it runs | Latency envelope | Concurrency and caching | Optimize |
|---|---|---|---|---|
| Interactive CLI | Operator laptop or jump host | Often WAN/VPN, about 100-800 ms RTT | One BMC, one-shot command, per-connection caching and keep-alive | Clear errors, bounded request count, no hidden writes |
| Exporter daemon | One process or pod per BMC | Usually in-rack or same-site | No cross-BMC fan-out; one stream per process, supervised by systemd or Kubernetes | Per-BMC scrape cost, health metrics, bounded sampler work |
| Kubernetes controller/operator | In-cluster controller polling `RedfishEndpoint` resources | Cluster-to-BMC network path | One reconcile loop per endpoint; Kubernetes stores observed status | Status freshness, safe retry/backoff, no automatic mutation without approval |
| Fleet CLI/proxy | Jump host or in-DC service | Mixed; usually closer to the BMC network than an operator laptop | Bounded read-only fan-out across BMCs; not a telemetry streamer | Per-node isolation, partial-failure reporting, request budgets |

The exporter daemon deliberately keeps many shallow liveness domains: one child process or pod can
fail, restart, or rotate credentials without disrupting the rest of the fleet. Aggregation belongs
in the OpenTelemetry Collector or Prometheus, not inside `redfish_ctl`.

Every completed scrape adds `hw.scrape.ok` and `hw.scrape.duration_seconds` so downstream alerts can
distinguish an alive exporter with a bad scrape from a missing process or route. Long-running
SignalFx push mode also offsets each sleep interval by plus or minus ten percent to avoid many BMCs
being polled at the same instant.

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
`hw.gpu.power`, `hw.gpu.temperature`, `hw.gpu.clock_mhz`, `hw.gpu.compute.utilization`,
`hw.gpu.throttle.duration_seconds`, `hw.gpu.memory.*`, `hw.leak.state`, and `hw.fabric.*`.
EnvironmentMetrics rollups add `resource_type` and `resource` dimensions so chassis, processor, and
memory power can be separated. `hw.leak.state`, derived from linked `LeakDetector` rows, is `0` for
clear detector states and `1` for warning or critical states. Fabric metrics include link state,
negotiated speed, RX/TX bytes, bandwidth, FEC/CRC-style counters when Redfish exposes them, and
other NVLink error counters.

## Credentials

For exporter runs, keep BMC credentials in environment variables or a gitignored runtime file. Do not
put the password on argv; the exporter rejects `--password` and `--idrac_password`.
Use `REDFISH_*` names for new files. Legacy `IDRAC_*` names remain accepted, but if both namespaces
are present for the same credential, different values fail closed instead of choosing a silent winner.

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

Override the identity math with `--identity-host-prefix`, `--identity-bmc-octet-base`,
`--identity-server-octet-base`, and `--identity-server-subnet`. The same settings can come from
`REDFISH_EXPORTER_HOST_PREFIX`, `REDFISH_EXPORTER_BMC_OCTET_BASE`,
`REDFISH_EXPORTER_SERVER_OCTET_BASE`, and `REDFISH_EXPORTER_SERVER_SUBNET`, which the exporter reads
from the process environment. A config spec can also carry them; the sample
`specs/exporter_signalfx_spec.json`, defined in this repository's `specs/` directory, uses the
`identity` object for these fields.

ThermalSubsystem temperature samples set `source=thermal-subsystem` and include `chassis`, `sensor`,
and `zone` dimensions. The `zone` dimension comes from Redfish `PhysicalContext` when present, or
falls back to the reported sensor name.

## SignalFx

SignalFx push mode uses `SPLUNK_ACCESS_TOKEN`, the ingest token read from the process environment,
and `SPLUNK_INGEST_URL`, the ingest URL read from the process environment. The ingest URL must be the
full SignalFx datapoint endpoint ending in `/v2/datapoint` (for example
`https://ingest.us1.signalfx.com/v2/datapoint`); the exporter POSTs it verbatim, so a bare host such
as `https://ingest.us1.observability.splunkcloud.com` is rejected because it would accept the request
but silently drop every datapoint. Override the default with `--signalfx-ingest-url`.

For non-environment token sources, use `--signalfx-token-file` or `--signalfx-token`. The
`--signalfx-token-file` option reads the token from a local file created by the deployment step. A
config spec passed with `--exporter-config` can set `signalfx.ingest_url`, `signalfx.token_env`,
`signalfx.token_file`, or `signalfx.token`; explicit CLI values win over the spec, and the spec wins
over the default environment fallback.

Without `--once`, push mode scrapes and pushes on a loop every `--interval` seconds:

```bash
redfish_ctl exporter \
  --credential-file .internal/idrac_exporter.env \
  --exporter-config specs/exporter_signalfx_spec.json \
  --vendor supermicro \
  --output signalfx \
  --push-signalfx
```

Add `--once` to scrape, POST the datapoints exactly once, and return the pushed body plus the ingest
HTTP status:

```bash
redfish_ctl exporter \
  --credential-file .internal/idrac_exporter.env \
  --vendor supermicro \
  --once \
  --output signalfx \
  --push-signalfx
```

For a dry run, use `--once --output signalfx` without `--push-signalfx`. That prints the SignalFx
datapoint envelope without posting anything.

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
hw.gpu.temperature{gpu="GPU_0",sensor="HGX_GPU_0_TEMP_0",...} 32.9375
hw.gpu.clock_mhz{gpu="GPU_0",property="operating_speed",...} 2070
hw.gpu.memory.capacity_utilization{gpu="GPU_1",memory="GPU_1_DRAM_0",...} 91
hw.gpu.throttle.duration_seconds{gpu="GPU_0",property="power_limit",...} 0
hw.leak.state{detector="Chassis_0_LeakDetector_0_ColdPlate",...} 0
hw.fabric.link_up{fabric="nvlink",gpu="GPU_0",port="NVLink_0",...} 1
hw.fabric.rx_bytes{fabric="nvlink",gpu="GPU_0",port="NVLink_0",...} 9460179851686
```

No live write is involved. If the command fails, check credentials, BMC reachability, and whether the
BMC exposes the modern telemetry resources listed at the top of this page.
