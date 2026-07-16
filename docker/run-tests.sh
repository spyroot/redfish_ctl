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
previous_id="$(docker images -q "${IMAGE}" 2>/dev/null || true)"
docker build -f docker/Dockerfile.test -t "${IMAGE}" .

# Only a genuine dependency rebuild changes the image id and leaves the
# previous image untagged; reclaim dangling layers just for that case so the
# prune cannot race an unrelated concurrent build on a shared host.
if [ -n "${previous_id}" ] && [ "${previous_id}" != "$(docker images -q "${IMAGE}")" ]; then
  docker image prune -f >/dev/null
fi

echo ">> running offline test suite in Linux container (repo mounted at /work)"
# Run as the invoking user so anything written into the mounted repo (e.g.
# .pytest_cache) is never root-owned on a Linux host; HOME points at a
# writable location for the non-root uid. Pass extra pytest args through,
# e.g. ./docker/run-tests.sh -k boot
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "${REPO_ROOT}:/work" -w /work "${IMAGE}" pytest -q "$@"

echo ">> Linux test run complete"
