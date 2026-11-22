#!/usr/bin/env bash
#
# Build and verify the redfish_ctl distribution.
#
# Builds sdist + wheel and runs `twine check`. Publishing is handled only by
# the tag-triggered Trusted Publishing workflow.
#
#   ./build_dist.sh            # build + verify into dist/
#
# The version stamped on the artifact comes from redfish_ctl/version.py (the
# single source of truth the CLI also reports via --version).
set -euo pipefail

validate_args() {
    if (( $# > 1 )); then
        echo "build_dist: expected no arguments" >&2
        echo "Usage: ./build_dist.sh" >&2
        exit 2
    fi
    case "${1:-}" in
        "") return 0 ;;
        --help)
            echo "Usage: ./build_dist.sh"
            exit 0
            ;;
        --upload)
            echo "build_dist: manual PyPI upload is retired" >&2
            echo "Publish a validated v* tag through Trusted Publishing." >&2
            exit 2
            ;;
        *)
            echo "build_dist: unknown argument: $1" >&2
            echo "Usage: ./build_dist.sh" >&2
            exit 2
            ;;
    esac
}

validate_args "$@"

VERSION="$(python setup.py --version)"
echo ">> redfish_ctl version: ${VERSION}"

# Start from a clean dist/ so we never upload a stale artifact.
rm -rf dist build ./*.egg-info
python setup.py sdist bdist_wheel
python -m twine check dist/*

echo ">> Built and verified:"
ls -1 dist/
echo ">> Not uploaded. Publish only through the validated v* tag workflow."
