#!/usr/bin/env bash
# Shared resolver for the remote docker test fleet ("GB300" targets in the
# Makefile). Committed code carries NO internal addresses: the concrete
# values live in the gitignored .internal/inventory/gb300-fleet.env next to this repo
# (see TEAM_GUIDE.md, "GB300 Docker test environment"), or come from the
# caller's environment.
#
#   .internal/inventory/gb300-fleet.env defines:
#     GB300_USER        ssh login on the nodes
#     GB300_IP_BASE     first three octets of the node subnet
#     GB300_SLOT0_OCTET last octet of slot 0 (slot N = SLOT0_OCTET + N)
#     GB300_SLOTS       highest slot number (0-based count - 1)
#
# Usage:  gb300.sh host <slot>          -> prints user@ip for the slot
#         gb300.sh list                 -> prints every slot number
#         gb300.sh run <slot> <agent> <ref> <cmd...>   -> one-shot container
#         gb300.sh shell <slot> <agent>                -> interactive container
#
# run/shell mount the node's ~/.ssh/redfish_ctl_git and
# ~/.ssh/redfish_ctl_gh_token (staged by the operator) into
# /secrets ONLY when they exist, so a key-less node still runs read-only
# work (the entrypoint falls back to an https clone).
set -euo pipefail

_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_env_file="${GB300_ENV_FILE:-$_repo_root/.internal/inventory/gb300-fleet.env}"
if [ -f "$_env_file" ]; then
    # The env file supplies DEFAULTS: values already set in the caller's
    # environment (for example `make gb300-test GB300_IMAGE=...`) win over
    # it, so snapshot pre-set values and restore them after sourcing.
    _saved=""
    for _v in GB300_USER GB300_IP_BASE GB300_SLOT0_OCTET GB300_SLOTS \
              GB300_HOST GB300_IMAGE; do
        if [ -n "${!_v:-}" ]; then
            _saved="$_saved $_v=${!_v}"
        fi
    done
    # shellcheck disable=SC1090
    source "$_env_file"
    for _kv in $_saved; do
        export "${_kv?}"
    done
fi

_require() {
    local name="$1"
    if [ -z "${!name:-}" ]; then
        echo "gb300.sh: $name is not set — create .internal/inventory/gb300-fleet.env" \
             "(see TEAM_GUIDE.md, GB300 Docker test environment) or export it" >&2
        exit 2
    fi
}

# Caller-supplied values are embedded in remote shell strings, so they are
# validated against tight charsets up front: a stray space or metacharacter
# fails loudly here instead of malforming an ssh/docker command later.
_check() {
    local what="$1" value="$2" pattern="$3"
    if ! [[ "$value" =~ $pattern ]]; then
        echo "gb300.sh: invalid $what '$value' (must match $pattern)" >&2
        exit 2
    fi
}

cmd="${1:-}"
case "$cmd" in
    host)
        slot="${2:?usage: gb300.sh host <slot>}"
        _check slot "$slot" '^[0-9]+$'
        if [ -n "${GB300_HOST:-}" ]; then
            echo "${GB300_USER:+$GB300_USER@}$GB300_HOST"
            exit 0
        fi
        _require GB300_USER
        _require GB300_IP_BASE
        _require GB300_SLOT0_OCTET
        last=$((GB300_SLOT0_OCTET + slot))
        echo "$GB300_USER@$GB300_IP_BASE.$last"
        ;;
    list)
        _require GB300_SLOTS
        seq 0 "$GB300_SLOTS"
        ;;
    run|shell)
        slot="${2:?usage: gb300.sh $cmd <slot> <agent> ...}"
        agent="${3:?usage: gb300.sh $cmd <slot> <agent> ...}"
        _check slot "$slot" '^[0-9]+$'
        _check agent "$agent" '^[A-Za-z0-9._-]+$'
        host="$("$0" host "$slot")"
        # Image selection: an explicit GB300_IMAGE wins; otherwise the node
        # picks the credentialed agent image when it has been provisioned and
        # falls back to the public base loudly, so a fresh setup works before
        # the operator stages credentials.
        if [ -n "${GB300_IMAGE:-}" ]; then
            _check image "$GB300_IMAGE" '^[A-Za-z0-9._/:-]+$'
            image_snippet="img=$GB300_IMAGE;"
        else
            image_snippet='img=redfish-ctl-agent;
docker image inspect "$img" >/dev/null 2>&1 || { echo "gb300: agent image not on this node yet, using base redfish-ctl-dev" >&2; img=redfish-ctl-dev; };'
        fi
        # The remote snippet assembles the secret mounts on the node itself so
        # a missing key file never turns into a root-owned bind-mount dir.
        remote_prefix='m="";
[ -f "$HOME/.ssh/redfish_ctl_git" ] && m="$m -v $HOME/.ssh/redfish_ctl_git:/secrets/git_key:ro";
[ -f "$HOME/.ssh/redfish_ctl_gh_token" ] && m="$m -v $HOME/.ssh/redfish_ctl_gh_token:/secrets/gh_token:ro";'
        if [ "$cmd" = "shell" ]; then
            exec ssh -t "$host" "$remote_prefix $image_snippet docker run -it --rm \
                --name rfctl-$agent -v rfctl-work-$agent:/work \$m \$img bash"
        fi
        ref="${4:?usage: gb300.sh run <slot> <agent> <ref> <cmd...>}"
        _check ref "$ref" '^[A-Za-z0-9._/-]+$'
        shift 4
        [ $# -gt 0 ] || { echo "gb300.sh run: no command given" >&2; exit 2; }
        printf -v quoted '%q ' "$@"
        exec ssh "$host" "$remote_prefix $image_snippet docker run --rm \
            -v rfctl-work-$agent:/work -e RFCTL_REF=$ref \$m \$img bash -lc $(printf '%q' "$quoted")"
        ;;
    *)
        echo "usage: gb300.sh {host <slot>|list|run <slot> <agent> <ref> <cmd...>|shell <slot> <agent>}" >&2
        exit 2
        ;;
esac
