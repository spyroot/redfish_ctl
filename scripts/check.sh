#!/usr/bin/env bash
# check.sh — the single entry point for the gate registry (gates/manifest.yaml).
#
#   check.sh --list                         enumerate every registered gate
#   check.sh --profile <name>               run all gates in a profile
#   check.sh --profile <name> --gate <id>   run one gate in that profile
#   check.sh --profile merge [--gate <id>] --dispatch [--dry-run]
#   check.sh --profile merge [--gate <id>] --dispatch --apply \
#            --confirm-project-ci-run [Builder controls]
#                                   (merge|integration|scheduled|deploy|repository-export)
#
# EXECUTION AUTHORITY = Kubernetes. Outside a cluster pod, check.sh REFUSES to run tests locally and
# prints guarded Builder plan/apply commands — it never runs a gate on the operator's laptop.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Kubernetes is the execution authority, so this predicate is a safety guard, not a convenience.
# Deciding it from ONE environment variable is too weak: exporting KUBERNETES_SERVICE_HOST on a
# workstation would run the whole profile on the laptop. Three independent kinds of evidence are
# required instead:
#   1. BOTH master-service variables, which the kubelet injects together — never just one;
#   2. a readable /proc/1/cgroup — Linux process evidence that cannot exist on the operator's macOS;
#   3. at least one artifact only a kubelet produces (_kubelet_evidence below).
# There is deliberately NO override variable: an escape hatch is how a guard stops being a guard.
_kubelet_evidence() {
  # An OR on purpose. A pod with automountServiceAccountToken:false (platform/agent-runner/job.yaml)
  # has no service-account files, and a cgroup-v2 pod with a private cgroup namespace reads only
  # "0::/", so requiring any single one of these would refuse to run inside a legitimate Job.
  [ -r /var/run/secrets/kubernetes.io/serviceaccount/token ] ||
    [ -r /var/run/secrets/kubernetes.io/serviceaccount/namespace ] ||
    grep -qs 'svc\.cluster\.local' /etc/resolv.conf ||
    grep -qsE 'kubernetes\.io~|kubelet/pods' /proc/self/mountinfo ||
    grep -qs 'kubepods' /proc/1/cgroup
}

_in_cluster() {
  [ -n "${KUBERNETES_SERVICE_HOST:-}" ] &&
    [ -n "${KUBERNETES_SERVICE_PORT:-}" ] &&
    [ -r /proc/1/cgroup ] &&
    _kubelet_evidence
}

# Summary: Resolve the exact Builder-owned project CI entrypoint.
# Arguments: none. Environment: none. Stdout: executable path.
# Stderr: classified blocker text. Exit classes: 0 resolved, 78 blocked.
# Side effects: read-only binding and Git inspection. Idempotency: deterministic.
# Cleanup: none.
_builder_project_ci() {
  local provider_root provider_revision provider_head project_ci
  local discovery_program discovery_argument provider_map
  local required_command
  for required_command in yq jq; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
      echo "BLOCKER: Builder dispatch requires $required_command from the approved toolbox" >&2
      return 78
    fi
  done
  if [ ! -r builder-binding.yaml ]; then
    echo "BLOCKER: tracked builder-binding.yaml is required for dispatch" >&2
    return 78
  fi
  provider_root="$(yq -er '.spec.source.localPath' builder-binding.yaml)" || return 78
  provider_revision="$(yq -er '.spec.source.revision' builder-binding.yaml)" || return 78
  if ! provider_head="$(git -C "$provider_root" rev-parse HEAD 2>/dev/null)"; then
    echo "BLOCKER: Builder checkout from builder-binding.yaml is unavailable" >&2
    return 78
  fi
  if [ "$provider_head" != "$provider_revision" ]; then
    echo "BLOCKER: Builder checkout HEAD does not match the bound provider revision" >&2
    return 78
  fi
  if ! git -C "$provider_root" diff --quiet "$provider_revision" -- \
    scripts inventory builder-binding.yaml schemas ci/templates; then
    echo "BLOCKER: Builder dispatch surfaces differ from the bound provider revision" >&2
    return 78
  fi
  project_ci="$provider_root/scripts/project-ci"
  if [ ! -x "$project_ci" ]; then
    echo "BLOCKER: bound Builder revision has no executable scripts/project-ci" >&2
    return 78
  fi
  if [ "$(yq -er '.spec.discovery.command | length' builder-binding.yaml)" -ne 2 ]; then
    echo "BLOCKER: Builder discovery binding must contain exactly two arguments" >&2
    return 78
  fi
  discovery_program="$(yq -er '.spec.discovery.command[0]' builder-binding.yaml)" || return 78
  discovery_argument="$(yq -er '.spec.discovery.command[1]' builder-binding.yaml)" || return 78
  if [ "$discovery_program" != "./scripts/shared_inventory_map.sh" ] || \
    [ "$discovery_argument" != "--validate" ]; then
    echo "BLOCKER: Builder discovery binding is not the approved validation command" >&2
    return 78
  fi
  provider_map="$(
    "$provider_root/${discovery_program#./}" "$discovery_argument"
  )" || return 78
  if ! jq -e '
    [.spec.providerCapabilities.capabilities[].id] as $ids |
    ($ids | index("ci.focused-gate")) != null and
    ($ids | index("ci.merge-profile")) != null
  ' >/dev/null <<<"$provider_map"; then
    echo "BLOCKER: bound Builder lacks focused and merge-profile capabilities" >&2
    return 78
  fi
  printf '%s\n' "$project_ci"
}

# Summary: Dispatch one exact-ref focused gate or the full merge profile.
# Arguments: profile and optional gate id. Environment: provider credential binding.
# Stdout: Builder's sanitized JSON result. Stderr: bounded diagnostics.
# Exit classes: Builder project-ci result. Side effects: none in dry-run; apply
# creates one Internal GitLab pipeline.
# Idempotency: exact project, ref, commit, and selection. Cleanup: provider-owned.
_dispatch() {
  local selected_profile="$1" selected_gate="$2"
  local project_ci ref commit
  local -a args
  if [ "$selected_profile" != "merge" ]; then
    echo "check.sh: --dispatch supports only the merge profile" >&2
    return 2
  fi
  if _in_cluster; then
    echo "check.sh: --dispatch is an off-cluster provider action" >&2
    return 2
  fi
  if ! ref="$(git symbolic-ref --quiet --short HEAD)"; then
    echo "BLOCKER: dispatch requires a named branch, not detached HEAD" >&2
    return 78
  fi
  commit="$(git rev-parse HEAD)"
  project_ci="$(_builder_project_ci)" || return "$?"
  args=(
    run
    --project redfish_ctl
    --host internal-gitlab
    --ref "$ref"
    --requested-commit "$commit"
  )
  if [ -n "$selected_gate" ]; then
    args+=(--gate "$selected_gate")
  else
    # Builder calls the protected exact-head merge surface its full profile.
    # The selected project command remains scripts/check.sh --profile merge.
    args+=(--profile full)
  fi
  if $dispatch_apply; then
    args+=(--apply --confirm-project-ci-run)
  else
    args+=(--dry-run)
  fi
  if [ "${#dispatch_args[@]}" -gt 0 ]; then
    args+=("${dispatch_args[@]}")
  fi
  args+=(--json)
  exec "$project_ci" "${args[@]}"
}

_list() {
  python3 - <<'PY'
import pathlib, yaml
reg = yaml.safe_load(pathlib.Path("gates/manifest.yaml").read_text())
print(f"{'ID':30} {'PROFILE':12} {'MUTATES':8} COMMAND")
for g in reg["gates"]:
    print(f"{g['id']:30} {g['profile']:12} {str(g['mutates']):8} {g['command']}")
print(f"\n{len(reg['gates'])} gates; mandatory: {len(reg['mandatory_ids'])}; runner_tag: {reg['runner_tag']}")
PY
}

usage() {
  cat <<'USAGE'
usage: check.sh --list
       check.sh --profile <merge|integration|scheduled|deploy|repository-export>
                [--gate <id>] [--dispatch [--dry-run]]
                [--dispatch --apply --confirm-project-ci-run]
                [--no-wait] [--log-format text|json]
                [--log-level debug|info|warning|error] [--log-file PATH]
                [--run-id ID] [--timeout SECONDS]

A focused --gate result proves only that gate at the exact commit. It is not
merge or release evidence; use the full required pipeline for those decisions.
USAGE
}

list=false
profile=""
gate=""
dispatch=false
dispatch_apply=false
dispatch_confirm=false
dispatch_dry_run=false
dispatch_args=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --list)
      list=true
      shift
      ;;
    --profile)
      profile="${2:?check.sh: --profile requires a value}"
      shift 2
      ;;
    --gate)
      gate="${2:?check.sh: --gate requires a value}"
      shift 2
      ;;
    --dispatch)
      dispatch=true
      shift
      ;;
    --dry-run)
      dispatch_dry_run=true
      shift
      ;;
    --apply)
      dispatch_apply=true
      shift
      ;;
    --confirm-project-ci-run)
      dispatch_confirm=true
      shift
      ;;
    --no-wait)
      dispatch_args+=(--no-wait)
      shift
      ;;
    --log-format|--log-level|--log-file|--run-id|--timeout)
      dispatch_args+=("$1" "${2:?check.sh: $1 requires a value}")
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "check.sh: unexpected argument" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if $list; then
  if [ -n "$profile" ] || [ -n "$gate" ] || $dispatch; then
    echo "check.sh: --list cannot be combined with --profile, --gate, or --dispatch" >&2
    exit 2
  fi
  _list
  exit 0
fi

if [ -z "$profile" ]; then
  echo "check.sh: --profile is required when running gates" >&2
  usage >&2
  exit 2
fi

if { [ "${#dispatch_args[@]}" -gt 0 ] || $dispatch_apply || \
  $dispatch_confirm || $dispatch_dry_run; } && ! $dispatch; then
  echo "check.sh: Builder controls require --dispatch" >&2
  exit 2
fi

if $dispatch_apply && ! $dispatch_confirm; then
  echo "check.sh: --apply requires --confirm-project-ci-run" >&2
  exit 2
fi
if ! $dispatch_apply && $dispatch_confirm; then
  echo "check.sh: --confirm-project-ci-run requires --apply" >&2
  exit 2
fi
if $dispatch_apply && $dispatch_dry_run; then
  echo "check.sh: --dry-run and --apply are mutually exclusive" >&2
  exit 2
fi

if $dispatch; then
  _dispatch "$profile" "$gate"
fi

runner_args=(--profile "$profile")
if [ -n "$gate" ]; then
  runner_args+=(--gate "$gate")
fi

if _in_cluster; then
  exec ./scripts/gates/run.sh "${runner_args[@]}"
fi

ref="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
echo "check.sh: REFUSING to run gates locally — Kubernetes is the execution authority." >&2
echo "  Plan the exact branch through Builder:" >&2
echo "  ./scripts/check.sh --profile merge --dispatch" >&2
echo "  Apply after review:" >&2
echo "  ./scripts/check.sh --profile merge --dispatch --apply --confirm-project-ci-run" >&2
echo "  The protected pipeline then runs on the homelab-k8s runner for $ref." >&2
echo "  (or run inside a homelab-k8s runner/Job — a pod is detected from the kubelet's own" >&2
echo "   evidence, not from an environment variable, so exporting one cannot bypass this)" >&2
exit 3
