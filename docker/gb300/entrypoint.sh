#!/bin/bash
# Entrypoint for the redfish-ctl-dev image (docker/Dockerfile.gb300-dev).
#
# Wires up the read-only secrets the run targets bind-mount under /secrets,
# bootstraps the per-agent /work clone, then execs the requested command with
# the redfish_ctl conda env active. Secrets stay out of image layers: the SSH
# key is copied to ~/.ssh with 600 perms (a read-only bind mount cannot be
# chmod'ed in place), the gh token is exported for gh/API use.
#
#   /secrets/git_key    -> ~/.ssh/id_git + GIT_SSH_COMMAND     (git push/pull)
#   /secrets/gh_token   -> GH_TOKEN                            (gh pr ...)
#
# Environment knobs (set by the gb300-* Makefile targets):
#   RFCTL_REPO   clone URL   (default: git@github.com:spyroot/redfish_ctl.git)
#   RFCTL_REF    ref to test (default: main; fetched + hard-reset when set)
#   RFCTL_GIT_NAME / RFCTL_GIT_EMAIL   commit identity for work done inside
set -euo pipefail

if [ -f /secrets/git_key ]; then
    mkdir -p "$HOME/.ssh"
    cp /secrets/git_key "$HOME/.ssh/id_git"
    chmod 600 "$HOME/.ssh/id_git"
    export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/id_git -o IdentitiesOnly=yes"
fi
if [ -f /secrets/gh_token ]; then
    GH_TOKEN="$(tr -d '[:space:]' < /secrets/gh_token)"
    export GH_TOKEN
fi
# Observability ingest credentials (optional): the telemetry exporter reads
# SPLUNK_ACCESS_TOKEN / SPLUNK_INGEST_URL, so a container can push and
# live-verify metrics without any per-run token plumbing. An explicit
# SPLUNK_INGEST_URL from the caller wins over the realm-derived default.
if [ -f /secrets/splunk_token ]; then
    SPLUNK_ACCESS_TOKEN="$(tr -d '[:space:]' < /secrets/splunk_token)"
    export SPLUNK_ACCESS_TOKEN
fi
# BMC endpoint credentials (optional, operator-staged env file with
# REDFISH_IP/REDFISH_USERNAME/REDFISH_PASSWORD): lets a container run the
# read-only telemetry push gate without per-run credential plumbing. A
# caller-provided REDFISH_IP wins — the file only fills an empty environment.
if [ -f /secrets/bmc_env ] && [ -z "${REDFISH_IP:-}" ]; then
    set -a
    # shellcheck disable=SC1091
    . /secrets/bmc_env
    set +a
fi
if [ -f /secrets/splunk_realm ]; then
    _realm="$(tr -d '[:space:]' < /secrets/splunk_realm)"
    if [ -n "$_realm" ]; then
        # Realm feeds both the ingest URL default and the API host used by
        # tools/splunk_metric_gate.py; caller-provided values win.
        if [ -z "${SPLUNK_O11Y_REALM:-}" ]; then
            export SPLUNK_O11Y_REALM="$_realm"
        fi
        if [ -z "${SPLUNK_INGEST_URL:-}" ]; then
            export SPLUNK_INGEST_URL="https://ingest.${_realm}.signalfx.com/v2/datapoint"
        fi
    fi
fi

source /opt/conda/etc/profile.d/conda.sh
conda activate redfish_ctl

git config --global --add safe.directory /work 2>/dev/null || true
if [ -n "${RFCTL_GIT_NAME:-}" ]; then
    git config --global user.name "$RFCTL_GIT_NAME"
fi
if [ -n "${RFCTL_GIT_EMAIL:-}" ]; then
    git config --global user.email "$RFCTL_GIT_EMAIL"
fi

# Default clone transport: SSH when a git key was mounted (push works),
# read-only https otherwise so a key-less node can still run tests.
if [ -z "${RFCTL_REPO:-}" ]; then
    if [ -f /secrets/git_key ]; then
        RFCTL_REPO="git@github.com:spyroot/redfish_ctl.git"
    else
        RFCTL_REPO="https://github.com/spyroot/redfish_ctl.git"
    fi
fi
if [ ! -d /work/.git ]; then
    echo "gb300-dev: cloning $RFCTL_REPO into /work" >&2
    git clone "$RFCTL_REPO" /work
    (cd /work && pip install --quiet -e ".[dev]")
fi
if [ -n "${RFCTL_REF:-}" ]; then
    echo "gb300-dev: checking out $RFCTL_REF" >&2
    cd /work
    git fetch origin "$RFCTL_REF"
    git checkout -q FETCH_HEAD
    pip install --quiet -e ".[dev]"
fi

cd /work
exec "$@"
