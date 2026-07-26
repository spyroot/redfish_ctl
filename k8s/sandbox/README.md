# Kubernetes Sandbox

The `make k8s-sandbox` target runs an opt-in local smoke with `kind`. It covers
`RedfishEndpoint` reads and the corpus mock's controlled `RedfishNodeProfile`
plan, approve, apply, and converge path. By default it builds the corpus mock
BMC from `docker/Dockerfile.mock-bmc`, the DMTF DSP2043 simulator from
`docker/Dockerfile.dmtf-sim`, and the controller from
`docker/Dockerfile.controller`. It loads those images into the cluster named
by `KIND_CLUSTER_NAME`, read by `k8s/sandbox/run-sandbox.sh` and defaulting to
`redfish-sandbox`, then applies both sample `RedfishEndpoint` resources.

Required local tools:

- `docker`
- `git-lfs` (the DMTF DSP2043 bundle is LFS-tracked)
- `kind`
- `kubectl`

A fresh checkout must hydrate the pinned DSP2043 bundle before the first build:

```bash
git lfs pull --include=spec/dmtf/redfish/2026.1/mockups/DSP2043_2026.1.zip
```

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

The smoke check waits until `.status.powerState` is populated on the selected
sample resources. When `dmtf-sim` is selected, it also requires the endpoint to
report `ProfileResolved=True/DmtfProfileSelected` and
`Ready=True/PollSucceeded`. The corpus mock BMC serves only the committed GB300
corpus; the DMTF simulator
serves the pinned DSP2043 `public-rackmount1` profile and rejects mutating HTTP
verbs; the iLO backend is an emulator service, not a live BMC.

Select an explicit backend set with `SANDBOX_BACKENDS`, consumed by
`k8s/sandbox/run-sandbox.sh` and defaulting to `corpus-mock,dmtf-sim`.
Supported values are `corpus-mock`, `dmtf-sim`, `ilo-sim`, and `all`:

```bash
SANDBOX_BACKENDS=dmtf-sim make k8s-sandbox
```

That DMTF-only selection is GET-only and does not run the corpus mock's
`RedfishNodeProfile` mutation path.

Successful runs delete the sandbox cluster by default. To retain it for
inspection or reuse, set `KEEP_CLUSTER=1`, which `k8s/sandbox/run-sandbox.sh`
reads before cleanup, then remove it through the cleanup target when finished:

```bash
KEEP_CLUSTER=1 make k8s-sandbox
make k8s-sandbox-down
```
