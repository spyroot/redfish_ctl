#!/usr/bin/env bash
#
# Build (and optionally publish) the redfish_ctl distribution.
#
# Safe by default: builds sdist + wheel and runs `twine check`, but does NOT
# upload. Publishing to PyPI is irreversible (versions are immutable), so the
# upload only happens when you pass --upload explicitly.
#
#   ./build_dist.sh            # build + verify into dist/  (no upload)
#   ./build_dist.sh --upload   # build + verify + twine upload dist/*
#
# The version stamped on the artifact comes from redfish_ctl/version.py (the
# single source of truth the CLI also reports via --version).
set -euo pipefail

UPLOAD=0
[[ "${1:-}" == "--upload" ]] && UPLOAD=1

VERSION="$(python setup.py --version)"
echo ">> redfish_ctl version: ${VERSION}"

# Start from a clean dist/ so we never upload a stale artifact.
rm -rf dist build ./*.egg-info
python setup.py sdist bdist_wheel
python -m twine check dist/*

echo ">> Built and verified:"
ls -1 dist/

if [[ "${UPLOAD}" -eq 1 ]]; then
    echo ">> Uploading to PyPI (irreversible)..."
    python -m twine upload dist/*
    echo ">> Uploaded redfish_ctl ${VERSION}. Tag the release: git tag v${VERSION} && git push origin v${VERSION}"
else
    echo ">> Not uploaded. Re-run with --upload to publish, e.g.: ./build_dist.sh --upload"
fi
