# Kubernetes sandbox quickstart

Bring up a local Kubernetes control loop that drives real (or mock) BMCs through
`redfish_ctl`, end to end, on Docker Desktop. Every Redfish read below goes
through the tool — the CLI, the controller, or the web explorer — never an ad-hoc
script or `curl`. The only non-tool commands are the Kubernetes plumbing
(`kind`, `kubectl`, `make`), which stands up the cluster itself.

## What gets deployed

```
BMC ──(read-only poll via redfish_ctl)──▶ controller ──writes .status──▶ RedfishEndpoint CR
                                                                              │
                                    ┌─────────────────────────────────────────┴──────────┐
                                    ▼                                                       ▼
                       fleet-status consumer                                   redfish_ctl web explorer
              (reads CR status; dashboard/API/metrics)          (tree of the tool's commands, invoked live)
```

* **Controller** (`k8s/controller/`) polls each BMC read-only through the
  `redfish_ctl` command registry and writes power, health, temperature, and
  NIC/DPU firmware to a `RedfishEndpoint` custom resource's `.status`.
* **Fleet-status consumer** (`k8s/consumer/`) reads that CR status (never a BMC)
  and serves a dashboard, `GET /api/nodes`, and Prometheus `/metrics`.
* **Web explorer** (`k8s/explorer/`) serves a tree of the tool's read-only
  commands; selecting one invokes the real `redfish_ctl` command live against the
  target BMC via `sync_invoke`.

## Prerequisites

Docker Desktop (running), `kind`, and `kubectl` on `PATH`. Start Docker first:

```bash
open -a Docker            # macOS; wait until `docker info` succeeds
```

## 1. Stand up the sandbox (mock BMC) in one go

The sandbox target builds the images, creates the kind cluster, and deploys the
controller plus a mock BMC that serves the committed corpus. On success the
cluster is torn down automatically (a leftover kind cluster holds ~1.4GB RAM);
set `KEEP_CLUSTER=1` to keep it up — required before `make k8s-consumer` or
`make k8s-explorer`, which deploy into the running cluster. A failed run always
leaves the cluster up for diagnosis.

```bash
make k8s-sandbox                  # kind up → verify → kind down
KEEP_CLUSTER=1 make k8s-sandbox   # keep the cluster for k8s-consumer / iteration
make k8s-sandbox-down             # tear a kept cluster down when finished
```

The active kube context becomes `kind-redfish-sandbox`. Confirm the controller
populated a status:

```bash
kubectl config use-context kind-redfish-sandbox
kubectl -n redfish-sandbox get rfe -o wide
```

## 2. Point the controller at a real BMC

Create a credential Secret (never commit real credentials) and a
`RedfishEndpoint`. The address uses the RFC 5737 documentation range — replace it
with the BMC to poll:

```bash
kubectl -n redfish-sandbox create secret generic bmc-credentials \
  --from-literal=username=root --from-literal=password='<bmc-password>'

kubectl -n redfish-sandbox apply -f k8s/sandbox/redfish-endpoint-live-sample.yaml
kubectl -n redfish-sandbox get rfe -w        # watch it flip from Pending to a status
```

A kind pod on Docker Desktop inherits the host's routes, so validate BMC reachability
with a safe `redfish_ctl system` read before adding the endpoint. A full poll of a
large chassis (dozens of chassis, hundreds of sensors) takes a minute or more; the
endpoint shows `Pending` until the first walk finishes.

## 3. Deploy the fleet-status consumer

```bash
make k8s-consumer
kubectl -n redfish-sandbox port-forward svc/redfish-fleet-consumer 8199:80
open http://127.0.0.1:8199/
```

* `GET /api/nodes` — summary + per-node status (power, health, temperature, and
  `nicFirmwareVersions` / `nicFirmware[]`).
* `GET /api/nodes/<name>` — one endpoint.
* `GET /metrics` — Prometheus gauges: `redfish_endpoint_power_on`,
  `redfish_endpoint_temperature_max_celsius`, `redfish_nic_firmware_info`,
  `redfish_fleet_nic_firmware_drift_components` (0 when every node runs the same
  firmware per component).

## 4. Explore the tool live from the browser

The explorer turns the tool's command surface into a browsable tree; each click
runs the real command against the target BMC. Point it at a node and its Secret:

```bash
REDFISH_IP=203.0.113.10 REDFISH_PORT=443 REDFISH_SCHEME=https \
  SECRET=bmc-credentials make k8s-explorer

kubectl -n redfish-sandbox port-forward svc/redfish-ctl-explorer 8299:80
open http://127.0.0.1:8299/
```

Select **Network → NIC / DPU firmware**; the explorer invokes
`redfish_ctl nic-firmware` via `sync_invoke(ApiRequestType.NicFirmware, …)` and
shows the ConnectX/BlueField firmware versions. Only read-only commands are
allow-listed, so a mutating action is refused (HTTP 400).

## Query without Kubernetes — the same tool, on the CLI

Everything the controller and explorer read is a plain `redfish_ctl` command.
Set the endpoint once and read directly:

```bash
export REDFISH_IP=203.0.113.10 REDFISH_USERNAME=root REDFISH_PASSWORD='<pw>'
redfish_ctl nic-firmware --json_only      # NIC/DPU firmware for the 100GbE cards
redfish_ctl network-adapters              # the physical NIC/DPU cards
redfish_ctl firmware_inventory            # the full UpdateService firmware set
```

The web explorer is the same surface served over HTTP:
`python -m redfish_ctl.webui --port 8299` reads `REDFISH_IP`/`REDFISH_USERNAME`/
`REDFISH_PASSWORD` from the environment, exactly like the CLI.

## Tear down

```bash
kubectl -n redfish-sandbox delete rfe --all           # stop polling BMCs
kind delete cluster --name redfish-sandbox            # remove the cluster
```
