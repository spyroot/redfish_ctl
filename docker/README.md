# Docker Images

The `docker/Dockerfile` file in this directory builds the production `redfish_ctl` runtime image.
It installs `redfish_ctl[otlp]` from a wheel built inside the image build and runs the CLI as a
non-root user. Credentials are supplied at container runtime only; do not add them to the image.

| File | Purpose | Local command |
| --- | --- | --- |
| `docker/Dockerfile` | Production CLI/exporter image with the OTLP extra installed. | `docker build -f docker/Dockerfile -t redfish-ctl .` |
| `docker/Dockerfile.test` | Linux image for the offline pytest suite. | `./docker/run-tests.sh` |

## Runtime Environment

The `REDFISH_IP`, `REDFISH_USERNAME`, `REDFISH_PASSWORD`, and `REDFISH_PORT` variables are read by
`redfish_ctl/redfish_main.py` when the CLI starts. Put them in a local env file outside the repository:

```bash
REDFISH_IP=192.0.2.10
REDFISH_USERNAME=root
REDFISH_PASSWORD=replace-me
REDFISH_PORT=443
```

Run a one-shot read command, equivalent to `redfish_ctl system` on the host:

```bash
docker run --rm --env-file .env.redfish redfish-ctl system
```

Run the telemetry exporter with native OTLP output:

```bash
docker run --rm \
  --env-file .env.redfish \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://collector.example.invalid:4317 \
  redfish-ctl exporter --output otlp --once
```

The image entrypoint is `redfish_ctl`, so container arguments are the normal CLI subcommand and flags.
No Docker target uploads or pushes an image; publishing is handled only by the release workflow.
