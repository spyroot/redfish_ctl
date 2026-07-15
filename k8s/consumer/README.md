# Redfish Fleet Status Consumer

A small, read-only web application that **consumes** the output of the
RedfishEndpoint controller (`k8s/controller/redfish_endpoint_controller.py`).
The controller polls each BMC read-only and writes a health/power/temperature
summary onto the `RedfishEndpoint` custom resource's `.status`. This app reads
that `.status` and presents the whole fleet as a dashboard, a JSON API, and
Prometheus metrics.

## Layering

Data flows one direction only. The consumer never talks to a BMC and never
reads a Secret.

```
   BMC ──(read-only poll: system/sensors/thermal/NIC firmware)──▶ controller
                                                         │ writes .status
                                                         ▼
                                          RedfishEndpoint custom resource
                                                         │ read .status (get/list/watch)
                                                         ▼
                                          redfish-fleet-consumer (this app)
                                            /  ·  /api/nodes  ·  /metrics
```

Because the consumer only needs `get/list/watch` on `redfishendpoints`, its RBAC
(`rbac.yaml`) grants exactly that — **no Secret access, no write verbs**. That is
the security boundary that lets a status dashboard be exposed more widely than
the credential-holding controller.

## Endpoints

| Path              | Returns                                                        |
| ----------------- | ------------------------------------------------------------- |
| `GET /`           | Live HTML dashboard (auto-refreshes every 10s)                |
| `GET /api/nodes`  | JSON `{summary, nodes[]}` for the whole namespace             |
| `GET /api/nodes/<name>` | JSON for one endpoint (404 if unknown)                  |
| `GET /metrics`    | Prometheus text: power/health/temperature/NIC-firmware gauges |
| `GET /healthz`    | Liveness/readiness probe                                       |

An endpoint the controller has not polled yet reports `state: "Pending"` with
null readings rather than an error.

## NIC / DPU firmware

The controller polls each BMC's `NetworkAdapters` and
`UpdateService/FirmwareInventory` and folds the network-adapter firmware into
`status.networkFirmware` (via the `redfish_ctl nic-firmware` command and the
`get_network_firmware` facade). Every GB300 carries ConnectX-8 (CX8) NICs and a
BlueField-3 DPU; the consumer surfaces their firmware versions so an operator can
spot **firmware drift** across the fleet at a glance:

* Dashboard: a **NIC Firmware** column (versions + card count; a node running more
  than one distinct version is highlighted) and a **NIC FW Drift** summary card.
* `GET /api/nodes`: `nicAdapterCount`, `nicCount`, `dpuCount`, `nicFirmwareCount`,
  `nicFirmwareVersions`, and per-component `nicFirmware[]` (`id`, `deviceClass`,
  `version`, `updateable`).
* `GET /metrics`: `redfish_nic_firmware_info{node,nic_id,device_class,version}`,
  `redfish_node_nic_count`, and `redfish_node_nic_firmware_distinct_versions`
  (the drift gauge — alert when it exceeds 1).

## Deploy

The consumer deploys into the same `redfish-sandbox` namespace as the controller.
Bring up the controller + a backend first (`make k8s-sandbox`), then:

```bash
./k8s/consumer/deploy.sh                 # build image, kind load, apply, wait
# or the Makefile target:
make k8s-consumer

# reach the dashboard:
kubectl --context kind-redfish-sandbox -n redfish-sandbox \
  port-forward svc/redfish-fleet-consumer 8199:80
open http://127.0.0.1:8199/
```

## Pointing the controller at a real BMC

The dashboard shows any `RedfishEndpoint` in the namespace, mock or live. To add
a real BMC, create a credential Secret and an endpoint — see
`k8s/sandbox/redfish-endpoint-live-sample.yaml`. Live endpoints are labelled
`backend: live` in the API and dashboard; in-cluster mocks are `backend: mock`.

## Tests

The pure rendering helpers (`normalize_endpoint`, `fleet_summary`,
`render_fleet_json`, `render_metrics`, `render_html`) carry no Kubernetes
dependency, so they are unit-tested offline in `tests/test_fleet_consumer.py`
(`pytest -q tests/test_fleet_consumer.py`). The Kubernetes client is imported
lazily inside `load_endpoints`, the same way the controller guards its `kopf`
import.
