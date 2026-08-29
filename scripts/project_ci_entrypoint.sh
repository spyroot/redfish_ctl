#!/usr/bin/env bash
# redfish_ctl command selector for the imported Builder CPU job.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Summary: Select exactly one project validation command.
# Arguments: none. Environment: FOCUSED_GATE and PROJECT_CI_PROFILE.
# Stdout: none. Stderr: bounded validation errors. Exit classes: 2 or check.sh.
# Side effects: delegates to the Kubernetes-guarded gate entrypoint.
# Idempotency and cleanup: inherited from the selected gate profile.
project_ci_main() {
  local focused_gate="${FOCUSED_GATE:-}"
  local selected_profile="${PROJECT_CI_PROFILE:-}"
  local -a args=(--profile merge)

  if [ -n "$focused_gate" ] && [ "$selected_profile" = "full" ]; then
    echo "project_ci_entrypoint.sh: focused gate and profile are mutually exclusive" >&2
    return 2
  fi
  case "$selected_profile" in
    ""|focused|full) ;;
    *)
      echo "project_ci_entrypoint.sh: unsupported Builder profile" >&2
      return 2
      ;;
  esac
  if [ -n "$focused_gate" ]; then
    args+=(--gate "$focused_gate")
  elif [ "$selected_profile" = "focused" ]; then
    echo "project_ci_entrypoint.sh: focused profile requires FOCUSED_GATE" >&2
    return 2
  fi
  exec ./scripts/check.sh "${args[@]}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  project_ci_main "$@"
fi
