# Fleet Redfish Proxy

Author: Mus <spyroot@gmail.com>

> Status: read-only core exists. The CLI works without this service.

For one server, I use `redfish_ctl` directly. For a fleet, I want one service that can reach the BMC
network, keep desired state, read observed state, and reconcile the two. Clients should not all need
direct routes to BMCs.

## What Exists

The CLI already has the pieces I would reuse:

- `RedfishManager`, defined in `redfish_ctl/redfish_manager.py`, for product-neutral HTTP.
- `IDracManager`, defined in `redfish_ctl/idrac_manager.py`, for Dell/iDRAC behavior and host-system
  selection.
- `redfish_ctl.proxy`, defined under `redfish_ctl/proxy/`, for the first dependency-light read proxy
  core and optional FastAPI route binding.
- Vendor-neutral reads such as `sensors`, `network-adapters`, `metric-reports`, `logs`,
  `secure-boot`, and `oem-info`.
- Four offline corpora: Dell, Supermicro GB300, HPE iLO, and generic DMTF Redfish.

What does not exist yet is the controller loop: desired-state storage, drift checks, ordered writes,
job tracking, and fleet-wide backoff.

## Why A Proxy

The main lesson from Ironic and baremetal-operator is simple: isolate the process that can talk to
BMCs. This proxy would keep that process in Python so it can reuse the current Redfish managers and
vendor profiles instead of rebuilding command behavior in another stack.

```text
clients / kubectl / CI
  -> redfish-proxy service
  -> desired + observed state store
  -> async reconcile workers using the current Redfish managers
  -> BMC management network
```

A Kubernetes CustomResourceDefinition (CRD), installed later by an operator, could expose the same
model to cluster users. The first useful version can be smaller: an API, a database, and workers.

## Read-Only Core

The first code increment is `redfish_ctl.proxy`, a dependency-light package in the Python source tree.
It keeps an in-memory `NodeRegistry`, accepts a caller-provided manager factory, and shapes existing
read commands into service responses. `NodeConfig.username` and `NodeConfig.password`, connection-only
fields defined by the registry entry, are omitted from list responses.

The optional FastAPI adapter, defined in `redfish_ctl/proxy/fastapi_app.py`, imports FastAPI only when
`create_app()` is called. That keeps the CLI install dependency-light while still providing the route
binding for deployments that install an ASGI runtime.

The routes below are bound by `create_app()`, the adapter function in
`redfish_ctl/proxy/fastapi_app.py`, and implemented by `ReadOnlyProxy`, the read facade in
`redfish_ctl/proxy/core.py`.

| Method | Path | Backing read |
| --- | --- | --- |
| `GET` | `/nodes` | Sanitized `NodeRegistry` inventory |
| `GET` | `/nodes/{node_id}` | `redfish_ctl.api.get_system()` + `get_thermal()` |
| `GET` | `/nodes/{node_id}/sensors` | `sensors` command via `redfish_ctl.api.get_sensors()` |
| `GET` | `/nodes/{node_id}/gpu-metrics` | `gpu-metrics` command |
| `GET` | `/nodes/{node_id}/bios?attr_filter=...` | `bios` command |
| `GET` | `/nodes/{node_id}/metrics` | Exporter `MetricSample` rows built from existing read commands |

These routes are read-only. They do not create desired state, patch BMC resources, or run Redfish
actions. The manager factory is intentionally supplied by the embedding service so credentials can
come from a secret store without being serialized into proxy responses.

## Stored State

Clients write the `spec` fields through the proxy API. The reconcile worker writes the `status`
fields after Redfish reads or confirmed jobs. Field names below are proposed, not implemented.

```yaml
spec:
  bmcAddress: redfish://10.0.0.5/redfish/v1/Systems/System.Embedded.1
  vendor: dell
  credentialsRef: secret-name
  desired:
    power: "On"
    bootOverride: "Pxe"
    bios: { SriovGlobalEnable: "Enabled" }
status:
  power: "On"
  health: "OK"
  lastReconciled: <timestamp>
  goodCredentials: true
  error: null
```

`credentialsRef` is the name of a Kubernetes Secret created by the installer or operator. That
Secret would hold `username` and `password` keys. The proxy would log only metadata such as whether
credentials worked.

The Dell-shaped `bmcAddress` is only an example. A real proxy needs the same host-system selection
now in `IDracManager`: on a GB300, host actions go to `System_0`, not the HGX baseboard member. On
HPE iLO, the common host and manager ids are `Systems/1` and `Managers/1`.

## Reconcile Rules

Redfish does not give every state change as a push stream. The controller would poll, compare
observed state with `spec.desired`, apply only missing changes, and back off on transient errors.
After a write, it would not claim convergence until the BMC or a Redfish job confirms the new state.

A target profile such as `rt-low-latency` would expand to concrete BIOS attributes and boot settings.
Profiles are easier to review than one-off attribute lists, but the proxy still has to turn them into
ordered Redfish operations. See [BIOS profiles](bios-profiles.md) for the CLI pattern.

`firmware-update` exists as a guarded SimpleUpdate command today. A future proxy would need the same
dry-run/confirm safety model plus stronger rollout controls before running firmware updates at fleet
scale.

## Security

- BMC credentials live in a Kubernetes Secret, referenced by name from `credentialsRef`; they are
  never stored inline in the desired spec and never printed.
- NetworkPolicy, the Kubernetes egress policy installed with the proxy, would restrict traffic to the
  BMC management CIDR.
- TLS verification can stay off on the BMC hop for isolated lab networks, or be enabled with a
  trusted certificate chain.
- RBAC, installed with the proxy service account, would cover only proxy resources and referenced
  Secrets.

## First Useful Version

The read-only core now covers:

- register a server,
- read observed state,
- list servers,
- expose sensor, GPU metric, BIOS, and exporter-sample metric reads through GET endpoints.

The next useful version should add persistent storage, desired power and boot override fields, and a
reconcile loop that handles one server at a time with SQLite or Postgres.

Bounded concurrency, simulator-backed benchmarks, and 1,000-node gates are separate work; see
[Scaling and benchmarks](scaling-and-benchmarks.md). The current offline and emulator test lanes are
documented in [Testing](testing.md). Vendor maturity is tracked in [Vendors](vendors.md).
