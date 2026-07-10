# Kubernetes Sandbox

The `make k8s-sandbox` target runs an opt-in local read-path check with `kind`.
It builds the mock BMC image from `docker/Dockerfile.mock-bmc`, builds the
controller image from `docker/Dockerfile.controller`, loads both images into the
cluster named by `KIND_CLUSTER_NAME`, then applies the sample
`RedfishEndpoint` custom resource defined in `k8s/sandbox/redfish-endpoint-sample.yaml`.

Required local tools:

- `docker`
- `kind`
- `kubectl`

Run the sandbox from the repository root:

```bash
make k8s-sandbox
```

The smoke check waits until `.status.powerState` is populated on the sample
resource. The mock BMC serves only the committed GB300 corpus and rejects
mutating HTTP verbs.
