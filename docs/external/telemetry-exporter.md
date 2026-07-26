# Telemetry Exporter

Author: Mus <spyroot@gmail.com>

`redfish_ctl --vendor supermicro exporter`, defined in
`redfish_ctl/telemetry/supermicro/cmd_exporter.py`, is the read-only Supermicro/NV72 path for turning
BMC Redfish telemetry into metrics. See [Telemetry Metrics](telemetry-metrics.md) for the concrete
collector and metric catalog.

## Deployment Model: One Exporter Per BMC

The exporter follows one rule: **one exporter, one BMC, one metric stream.** A running exporter
polls a single BMC and publishes that BMC's metrics — nothing else. It never combines streams from
several servers; merging, routing, and fleet-wide views are the job of the tools built for that (an
OpenTelemetry Collector or Prometheus), which every telemetry pipeline already runs.

### On Kubernetes

Two long-running pieces cooperate:

- The **controller** (under `k8s/controller/`) watches `RedfishEndpoint` resources — one per BMC —
  and keeps each resource's status (power state, health, temperature summary) up to date for
  anything in the cluster that reads Kubernetes objects.
- One **exporter pod per BMC** streams that BMC's metrics. Each pod either pushes OTLP to the
  cluster's OpenTelemetry Collector (the standard agent/gateway most clusters already run — set
  `OTEL_EXPORTER_OTLP_ENDPOINT` to its service address) or serves `/metrics` for Prometheus to
  scrape per pod. Credentials come from a per-BMC Kubernetes Secret created by the operator or
  chart; see [Secrets](secrets.md).

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
ExecStart=/usr/local/bin/redfish_ctl --vendor supermicro exporter --output otlp
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

Rotating a credential means updating that BMC's file and restarting that one unit.

### Sizing

This model is sized for tens up to a couple hundred BMCs per site. Far beyond that, shard the
exporters across several hosts; do not look for a mode where one process owns the whole fleet —
that mode deliberately does not exist.

## Scrape Health

Every completed scrape adds `hw.scrape.ok` and `hw.scrape.duration_seconds` so downstream alerts can
distinguish an alive exporter with a bad scrape from a missing process or route. Long-running
SignalFx push mode also offsets each sleep interval by plus or minus ten percent to avoid many BMCs
being polled at the same instant.

## Credentials

For exporter runs, keep BMC credentials in environment variables or a gitignored runtime file. Do not
put the password on argv; the exporter rejects `--password` and `--idrac_password`.
Use `REDFISH_*` names for new files. Legacy `IDRAC_*` names remain accepted, but if both namespaces
are present for the same credential, different values fail closed instead of choosing a silent winner.

`redfish-exporter.env`, created by the operator before runtime and ignored by the repository's
`*.env` rule, is a simple `KEY=VALUE` file:

```bash
cat > redfish-exporter.env <<'EOF'
REDFISH_IP=192.0.2.29
REDFISH_USERNAME=admin
REDFISH_PASSWORD=replace-with-runtime-secret
REDFISH_PORT=443
EOF
```

## Identity Configuration

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

Set `--deployment-environment nv72-gb300`, defined by the exporter command, when dashboards or
detectors join Redfish hardware metrics with another producer by deployment environment. The value
is normalized to lowercase and emitted as both `deployment.environment` and
`deployment.environment.name` unless `--deployment-environment-compat deprecated|stable` narrows the
compatibility mode. Use `--require-deployment-environment` in a fleet manifest to fail startup when
that join key is missing. For a bounded extra label, use `--dimension telemetry.source=redfish`; it
cannot override identity labels or carry URL/token-shaped values.

`service.name` (default `redfish_ctl`) is the OTel logical service name. Every series carries it as a
label so a dashboard can separate this exporter's hardware metrics from other producers in the same
environment, and it also becomes the OTLP resource `service.name`. Override it with `--service-name`
or `REDFISH_EXPORTER_SERVICE_NAME` only when a deployment runs the exporter under a different service
identity.

The remaining producer identity fields are OTLP resource attributes only; they do not create
Prometheus labels or SignalFx dimensions. `--service-namespace` optionally groups related services,
`--service-version` identifies the deployed component version, and `--service-criticality` records
operational importance. `--service-instance-id` identifies this exporter process. When it is unset,
the exporter derives a stable UUID from the first usable Manager UUID, BMC/DC-SCM chassis serial, or
globally administered management MAC exposed by Redfish; a random UUID fallback is used only when no
stable source exists. Config files use `service_namespace`, `service_instance_id`, `service_version`,
and `service_criticality` inside the `identity` object. Their canonical environment settings are
`REDFISH_EXPORTER_SERVICE_NAMESPACE`, `REDFISH_EXPORTER_SERVICE_INSTANCE_ID`,
`REDFISH_EXPORTER_SERVICE_VERSION`, and `REDFISH_EXPORTER_SERVICE_CRITICALITY`, each defined by
`specs/config/environment.yaml`.

The default derivation assumes the supported deployment contract of one exporter process per BMC,
which keeps the instance identity stable across process restarts and BMC address changes. An
active-active deployment that intentionally runs multiple exporter processes against the same BMC
must assign each process a distinct `--service-instance-id`. If a canonical environment setting and
its deprecated `IDRAC_EXPORTER_*` alias disagree, startup fails unless an explicit CLI or config-file
value selects the intended setting.

## Prometheus

The default mode serves Prometheus text at `/metrics`:

```bash
redfish_ctl --vendor supermicro exporter \
  --credential-file redfish-exporter.env \
  --listen 0.0.0.0 \
  --port 9109
```

For a local smoke read, render once and exit:

```bash
redfish_ctl --vendor supermicro exporter \
  --credential-file redfish-exporter.env \
  --once \
  --output prometheus
```

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
redfish_ctl --vendor supermicro exporter \
  --credential-file redfish-exporter.env \
  --exporter-config specs/exporter_signalfx_spec.json \
  --output signalfx \
  --push-signalfx
```

Add `--once` to scrape, POST the datapoints exactly once, and return the pushed body plus the ingest
HTTP status:

```bash
redfish_ctl --vendor supermicro exporter \
  --credential-file redfish-exporter.env \
  --once \
  --output signalfx \
  --push-signalfx
```

Add `--verify-readback` for an ingestion verdict from Splunk Metric Time Series. That mode also
requires `--signalfx-realm` (or `SPLUNK_O11Y_REALM`) and the API read token named by
`--signalfx-api-token-env` (default `SPLUNK_API_TOKEN`).

For a dry run, use `--once --output signalfx` without `--push-signalfx`. That prints the SignalFx
datapoint envelope without posting anything. The reader's declared sample types place instantaneous
metrics under `gauge` and monotonic totals under `cumulative_counter`.

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
redfish_ctl --vendor supermicro exporter --output otlp --interval 30  # push loop
redfish_ctl --vendor supermicro exporter --output otlp --once         # push once
```

For dimensions, Sum/Gauge semantics, expected metric families, and a healthy
sample, see [Telemetry Metrics](telemetry-metrics.md#dimensions).
