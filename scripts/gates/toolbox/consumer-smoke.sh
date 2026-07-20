#!/usr/bin/env bash
# toolbox.consumer-smoke (merge, mutates:false): baseline proof that the shared
# toolbox image can actually run this project.
#
# Contract split it enforces, from the builder toolbox protocol:
#   the IMAGE supplies the runtime  -> conda, git-lfs
#   the PROJECT supplies its deps   -> environment.yml, created at run time
#
# So the toolbox can be deleted and rebuilt at any time and this gate answers one
# question: is the image we were handed still usable by redfish_ctl? It is a
# smoke test, not a tool inventory — builder owns the full required-tools list and
# its own gate. Duplicating that list here would fork the contract.
#
# A missing RUNTIME tool is BLOCKED, never repaired locally: installing it would
# hide a provider defect and create the second toolchain the protocol forbids.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

fail=0

# --- runtime, owned by the shared image -------------------------------------
for tool in conda git-lfs; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "BLOCKED: shared toolbox missing $tool" >&2
        echo "  The consumer does not install it. Fix lands in builder:" >&2
        echo "  docs/agents/protocols/ci-toolbox.md 'Cross-Project Toolchain Change Protocol'" >&2
        fail=1
    fi
done
[ "$fail" -eq 0 ] || exit 1

# --- the project's own declaration ------------------------------------------
[ -f environment.yml ] || { echo "toolbox.consumer-smoke: environment.yml is missing" >&2; exit 1; }

# The env file must be parseable and name the environment the pipeline activates.
env_name="$(yq -r '.name' environment.yml 2>/dev/null || true)"
[ -n "$env_name" ] && [ "$env_name" != "null" ] \
    || { echo "toolbox.consumer-smoke: environment.yml declares no name" >&2; exit 1; }

# --- dependencies, owned by the project -------------------------------------
# Resolved from the created environment, never from tools baked into an image.
# Absent here means the environment was not created or not activated, which is a
# pipeline defect in THIS repo, not a toolbox defect — so it fails rather than
# reporting BLOCKED.
missing=()
for tool in python pytest ruff; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if [ "${#missing[@]}" -gt 0 ]; then
    echo "toolbox.consumer-smoke: project tooling not on PATH: ${missing[*]}" >&2
    echo "  environment.yml declares these; the pipeline must create and activate" >&2
    echo "  the '$env_name' environment before gates run." >&2
    exit 1
fi

# Provenance is the evidence: a tool resolving from the conda prefix proves it
# came from the project environment. One resolving from /usr/bin would mean it
# was baked into the image — the exact thing this split exists to prevent.
prefix="${CONDA_PREFIX:-}"
if [ -n "$prefix" ]; then
    for tool in python pytest ruff; do
        p="$(command -v "$tool")"
        case "$p" in
            "$prefix"/*) : ;;
            *) echo "toolbox.consumer-smoke: $tool resolves to $p, outside the project environment ($prefix)" >&2
               echo "  Project dependencies must come from environment.yml, not the shared image." >&2
               exit 1 ;;
        esac
    done
fi

echo "toolbox.consumer-smoke: OK"
echo "  runtime  (image):   conda=$(command -v conda) git-lfs=$(command -v git-lfs)"
echo "  project  (env '$env_name'): python=$(command -v python) pytest=$(command -v pytest) ruff=$(command -v ruff)"
