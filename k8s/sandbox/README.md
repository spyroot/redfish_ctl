# Kubernetes Sandbox

The `make k8s-sandbox` target runs an opt-in local read-path check with `kind`.
By default it builds the corpus mock BMC image from `docker/Dockerfile.mock-bmc`,
builds the controller image from `docker/Dockerfile.controller`, loads both
images into the cluster named by `KIND_CLUSTER_NAME`, then applies the sample
`RedfishEndpoint` custom resource defined in `k8s/sandbox/redfish-endpoint-sample.yaml`.

Required local tools:

- `docker`
- `kind`
- `kubectl`

Run the sandbox from the repository root:

```bash
make k8s-sandbox
```

To run the same controller path against the HPE iLO Redfish emulator, add the
`ilo-sim` backend. The image is built locally from the public BSD-3 source at
<https://github.com/HewlettPackard/ilo-redfish-emulator> and uses its DL380a
mockup. The first build needs network access to fetch the pinned emulator tag:

```bash
SANDBOX_BACKENDS=corpus-mock,ilo-sim make k8s-sandbox
```

The smoke check waits until `.status.powerState` is populated on the sample
resources. The corpus mock BMC serves only the committed GB300 corpus and
rejects mutating HTTP verbs; the iLO backend is an emulator service, not a live
BMC.

Remove the default sandbox cluster when finished:

```bash
kind delete cluster --name redfish-sandbox
```
