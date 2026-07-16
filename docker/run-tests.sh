#!/usr/bin/env bash
# Build the deps-only Ubuntu test image (cached — it only rebuilds when the
# dependency manifests change) and run the offline redfish_ctl suite inside it
# against the repo MOUNTED at /work. Confirms Mac/Linux parity (Linux is
# case-sensitive; macOS is not) without creating a new image per code edit.
set -euo pipefail

IMAGE="${IMAGE:-redfish-ctl-test}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"

echo ">> building ${IMAGE} (ubuntu:24.04, dependency layers only — cached)"
docker build -f docker/Dockerfile.test -t "${IMAGE}" .

# A dependency rebuild leaves the previous image untagged; reclaim dangling
# layers quietly so repeated runs do not accumulate images on the host.
docker image prune -f >/dev/null

echo ">> running offline test suite in Linux container (repo mounted at /work)"
# Pass extra pytest args through, e.g. ./docker/run-tests.sh -k boot
docker run --rm -v "${REPO_ROOT}:/work" -w /work "${IMAGE}" pytest -q "$@"

echo ">> Linux test run complete"
