#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
usage: check.sh [--dry-run | --apply --confirm-smoke-inventory]
                [--confirm-overwrite] [--log-format text|json]
                [--log-level debug|info|warning|error] [--log-file PATH]
                [--run-id ID] [--timeout SECONDS]

Render inventory/ci/smoke-tests.yaml. The default is a non-mutating dry-run.
Replacing divergent inventory requires both --confirm-smoke-inventory and
--confirm-overwrite.
USAGE
}

# Render the canonical inventory to stdout.
# Arguments: none. Environment inputs: none. Stderr: none.
# Exit classes: 0 success. Side effects: none. Idempotency: deterministic bytes.
# Cleanup: none required.
render_smoke_inventory() {
  cat <<'YAML'
apiVersion: homelab.embedings.ai/v1alpha1
kind: CiSmokeInventory
metadata:
  name: redfish-ctl-required-ci-smoke-tests
spec:
  smokeTests:
    - job: gate-merge
      class: wiring
      command: './scripts/check.sh --profile "${MERGE_PROFILE:-merge}"'
      requiredTools: [bash, conda, git, git-lfs, python3]
      artifactUnderTest:
        type: repository
        digestSource: git-commit
      mutation: none
      timeoutSeconds: 3600
      evidencePath: reports/smoke/gate-merge.json
      cleanupPolicy: reports-only
      releaseBlocking: true

    - job: gate-integration
      class: protected-live
      command: ./scripts/check.sh --profile integration
      requiredTools: [bash, conda, git, git-lfs, jq, python3, yq]
      artifactUnderTest:
        type: repository-and-gitlab-integration
        digestSource: git-commit-and-pipeline
      mutation: read-only-api
      timeoutSeconds: 1800
      evidencePath: reports/smoke/gate-integration.json
      cleanupPolicy: reports-only
      releaseBlocking: true
YAML
}

# Report the change needed for an inventory path.
# Arguments: output path. Environment inputs: none. Stdout: create|replace|no-op.
# Stderr: classified path errors. Exit classes: 0 success, 1 invalid target.
# Side effects: none. Idempotency: repeated calls return the same action.
# Cleanup: none required.
inventory_action() {
  local output_path="$1"

  if [[ ! -e "$output_path" ]]; then
    printf 'create\n'
    return 0
  fi
  if [[ ! -f "$output_path" ]]; then
    printf 'check.sh: target exists but is not a regular file: %s\n' \
      "$output_path" >&2
    return 1
  fi
  if cmp -s <(render_smoke_inventory) "$output_path"; then
    printf 'no-op\n'
  else
    printf 'replace\n'
  fi
}

# Atomically create or replace the inventory after the caller confirms intent.
# Arguments: output path and precomputed create|replace|no-op action.
# Environment inputs: none. Stdout: none. Stderr: command diagnostics.
# Exit classes: 0 success, nonzero filesystem failure. Side effects: writes only
# the requested path. Idempotency: no-op leaves matching output untouched.
# Cleanup: a function-local subshell removes its temporary file on every exit.
write_smoke_inventory() (
  local output_path="$1"
  local action="$2"
  local output_dir
  local temporary_path=""

  if [[ "$action" == "no-op" ]]; then
    return 0
  fi
  output_dir="$(dirname "$output_path")"
  mkdir -p "$output_dir"
  temporary_path="$(mktemp "${output_path}.tmp.XXXXXX")"

  cleanup_smoke_inventory_temp() {
    if [[ -n "$temporary_path" && -e "$temporary_path" ]]; then
      rm -f -- "$temporary_path"
    fi
  }
  trap cleanup_smoke_inventory_temp EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  render_smoke_inventory >"$temporary_path"
  chmod 0644 "$temporary_path"
  mv -f -- "$temporary_path" "$output_path"
  temporary_path=""
)

emit_result() {
  local log_format="$1"
  local mode="$2"
  local action="$3"
  local output_path="$4"
  local run_id="$5"

  if [[ "$log_format" == "json" ]]; then
    printf '{"mode":"%s","action":"%s","path":"%s","run_id":"%s"}\n' \
      "$mode" "$action" "$output_path" "$run_id"
  else
    printf 'mode=%s action=%s path=%s run_id=%s\n' \
      "$mode" "$action" "$output_path" "$run_id"
  fi
}

main() {
  local mode="dry-run"
  local mode_was_set=false
  local confirm_inventory=false
  local confirm_overwrite=false
  local log_format="text"
  local log_level="info"
  local log_file=""
  local run_id="local"
  local timeout_seconds=30

  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --help|-h)
        usage
        return 0
        ;;
      --dry-run|--apply)
        if $mode_was_set; then
          printf 'check.sh: choose exactly one of --dry-run or --apply\n' >&2
          return 2
        fi
        mode="${1#--}"
        mode_was_set=true
        shift
        ;;
      --confirm-smoke-inventory)
        confirm_inventory=true
        shift
        ;;
      --confirm-overwrite)
        confirm_overwrite=true
        shift
        ;;
      --log-format)
        log_format="${2:?check.sh: --log-format requires a value}"
        shift 2
        ;;
      --log-level)
        log_level="${2:?check.sh: --log-level requires a value}"
        shift 2
        ;;
      --log-file)
        log_file="${2:?check.sh: --log-file requires a value}"
        shift 2
        ;;
      --run-id)
        run_id="${2:?check.sh: --run-id requires a value}"
        shift 2
        ;;
      --timeout)
        timeout_seconds="${2:?check.sh: --timeout requires a value}"
        shift 2
        ;;
      *)
        printf 'check.sh: unexpected argument: %s\n' "$1" >&2
        usage >&2
        return 2
        ;;
    esac
  done

  case "$log_format" in text|json) ;; *)
    printf 'check.sh: --log-format must be text or json\n' >&2
    return 2
  esac
  case "$log_level" in debug|info|warning|error) ;; *)
    printf 'check.sh: invalid --log-level: %s\n' "$log_level" >&2
    return 2
  esac
  if [[ ! "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
    printf 'check.sh: --timeout must be a positive integer\n' >&2
    return 2
  fi
  if [[ ! "$run_id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    printf 'check.sh: --run-id must contain only letters, digits, dot, underscore, or dash\n' >&2
    return 2
  fi
  if [[ -n "$log_file" ]]; then
    exec 2>>"$log_file"
  fi

  local repository_root
  local output_path
  local action
  repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  output_path="$repository_root/inventory/ci/smoke-tests.yaml"
  action="$(inventory_action "$output_path")" || return 1

  if [[ "$mode" == "dry-run" ]]; then
    emit_result "$log_format" "$mode" "$action" "$output_path" "$run_id"
    return 0
  fi
  if ! $confirm_inventory; then
    printf 'check.sh: --apply requires --confirm-smoke-inventory\n' >&2
    return 2
  fi
  if [[ "$action" == "replace" ]] && ! $confirm_overwrite; then
    printf 'check.sh: target differs; add --confirm-overwrite to replace it: %s\n' \
      "$output_path" >&2
    return 3
  fi

  write_smoke_inventory "$output_path" "$action"
  emit_result "$log_format" "$mode" "$action" "$output_path" "$run_id"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
