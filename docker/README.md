# Docker Images

The `docker/Dockerfile` file in this directory builds the production `redfish_ctl` runtime image.
It installs `redfish_ctl[otlp]` from a wheel built inside the image build and runs the CLI as a
non-root user. Credentials are supplied at container runtime only; do not add them to the image.

| File | Purpose | Local command |
| --- | --- | --- |
| `docker/Dockerfile` | Production CLI/exporter image with the OTLP extra installed. | `docker build -f docker/Dockerfile -t redfish-ctl .` |
| `docker/Dockerfile.test` | Linux image for the offline pytest suite. | `./docker/run-tests.sh` |
| `docker/Dockerfile.mock-bmc` | Read-only mock BMC image for the kind sandbox. | `docker build -f docker/Dockerfile.mock-bmc -t redfish-ctl-mock-bmc:local .` |
| `docker/Dockerfile.ilo-sim` | HPE iLO Redfish emulator image for the simulator sandbox backend. | `docker build -f docker/Dockerfile.ilo-sim -t redfish-ctl-ilo-sim:local .` |
| `docker/Dockerfile.controller` | RedfishEndpoint controller image for the kind sandbox. | `docker build -f docker/Dockerfile.controller -t redfish-ctl-controller:local .` |

## Runtime Environment

The `REDFISH_IP`, `REDFISH_USERNAME`, `REDFISH_PASSWORD`, and `REDFISH_PORT` variables are read by
`redfish_ctl/redfish_main.py` when the CLI starts. Put them in a local env file outside the repository:

```bash
REDFISH_IP=192.0.2.10
REDFISH_USERNAME=root
REDFISH_PASSWORD=replace-me
REDFISH_PORT=443
```

Run a one-shot Dell read command:

```bash
docker run --rm --env-file redfish.env redfish-ctl --vendor dell system
```

Run the telemetry exporter with native OTLP output:

```bash
docker run --rm \
  --env-file redfish.env \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://collector.example.invalid:4317 \
  redfish-ctl --vendor supermicro exporter --output otlp --once
```

The image entrypoint is `redfish_ctl`, so container arguments are the normal CLI subcommand and flags.
No Docker target uploads or pushes an image; publishing is handled only by the release workflow.
