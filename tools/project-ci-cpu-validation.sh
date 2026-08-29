#!/usr/bin/env bash
set -Eeuo pipefail

# Resolve Builder's one CPU resource job to exactly one consumer gate mode.
# Arguments: focused gate and project CI profile. Stdout: focused|full.
# Exit classes: 0 valid selection, 2 invalid or ambiguous dispatch.
# Side effects: none. Idempotency: deterministic for the same inputs.
project_ci_cpu_mode() {
  local focused_gate="$1"
  local project_profile="$2"

  if [[ -n "$focused_gate" ]]; then
    if [[ -n "$project_profile" && "$project_profile" != "focused" ]]; then
      printf 'BLOCKER: FOCUSED_GATE cannot be combined with PROJECT_CI_PROFILE=%s\n' \
        "$project_profile" >&2
      return 2
    fi
    printf 'focused\n'
    return 0
  fi

  case "$project_profile" in
    full)
      printf 'full\n'
      ;;
    focused)
      printf 'BLOCKER: PROJECT_CI_PROFILE=focused requires FOCUSED_GATE\n' >&2
      return 2
      ;;
    *)
      printf 'BLOCKER: PROJECT_CI_PROFILE must be focused or full\n' >&2
      return 2
      ;;
  esac
}

main() {
  local focused_gate="${FOCUSED_GATE:-}"
  local project_profile="${PROJECT_CI_PROFILE:-}"
  local mode

  mode="$(project_ci_cpu_mode "$focused_gate" "$project_profile")" || return "$?"
  case "$mode" in
    focused)
      exec ./scripts/check.sh --profile merge --gate "$focused_gate"
      ;;
    full)
      exec ./scripts/check.sh --profile merge
      ;;
    *)
      printf 'BLOCKER: unsupported CPU validation mode: %s\n' "$mode" >&2
      return 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
