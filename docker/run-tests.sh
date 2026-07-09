#!/usr/bin/env bash
# Build the Ubuntu test image and run the offline redfish_ctl suite inside it.
# Confirms Mac/Linux parity (Linux is case-sensitive; macOS is not).
set -euo pipefail

IMAGE="${IMAGE:-redfish-ctl-test}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"

echo ">> building ${IMAGE} (ubuntu:24.04)"
docker build -f docker/Dockerfile.test -t "${IMAGE}" .

echo ">> running offline test suite in Linux container"
# --cov optional; pass extra pytest args through, e.g. ./docker/run-tests.sh -k boot
docker run --rm "${IMAGE}" pytest -q "$@"

echo ">> Linux test run complete"
