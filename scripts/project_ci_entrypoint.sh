#!/usr/bin/env bash
# redfish_ctl command selector for the imported Builder CPU job.
set -Eeuo pipefail

project_ci_dry_run=false
project_ci_help=false
project_ci_log_format="text"
project_ci_log_level="info"
project_ci_log_file=""
project_ci_run_id=""

# Summary: Print the non-interactive project CI selector contract.
# Arguments: none. Environment: none. Stdout: usage text. Stderr: none.
# Exit classes: zero. Side effects: none. Idempotency: deterministic.
# Cleanup: none.
project_ci_usage() {
  printf '%s\n' \
    'usage: project_ci_entrypoint.sh [--dry-run]' \
    '                                [--log-format text|json]' \
    '                                [--log-level debug|info|warning|error]' \
    '                                [--log-file PATH] [--run-id ID]' \
    '' \
    'Select the complete merge profile, one FOCUSED_GATE, or the unit.all default' \
    "for Builder's focused profile. The selector never accepts credentials."
}

# Summary: Report a selector usage error without reflecting an unsafe value.
# Arguments: fixed diagnostic text. Environment: none. Stdout: none.
# Stderr: one bounded diagnostic plus usage. Exit classes: always 2.
# Side effects: none. Idempotency: deterministic. Cleanup: none.
project_ci_usage_error() {
  printf 'project_ci_entrypoint.sh: %s\n' "$1" >&2
  project_ci_usage >&2
  return 2
}

# Summary: Parse the universal non-mutating entrypoint controls.
# Arguments: command-line arguments. Environment: none. Stdout/stderr: none.
# Exit classes: zero or usage error 2. Side effects: updates selector globals.
# Idempotency: deterministic for the same argv. Cleanup: none.
project_ci_parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --help|-h)
        project_ci_help=true
        shift
        ;;
      --dry-run)
        project_ci_dry_run=true
        shift
        ;;
      --log-format)
        if [ "$#" -lt 2 ]; then
          project_ci_usage_error "--log-format requires a value"
          return 2
        fi
        project_ci_log_format="$2"
        shift 2
        ;;
      --log-level)
        if [ "$#" -lt 2 ]; then
          project_ci_usage_error "--log-level requires a value"
          return 2
        fi
        project_ci_log_level="$2"
        shift 2
        ;;
      --log-file)
        if [ "$#" -lt 2 ]; then
          project_ci_usage_error "--log-file requires a path"
          return 2
        fi
        project_ci_log_file="$2"
        shift 2
        ;;
      --run-id)
        if [ "$#" -lt 2 ]; then
          project_ci_usage_error "--run-id requires a value"
          return 2
        fi
        project_ci_run_id="$2"
        shift 2
        ;;
      *)
        project_ci_usage_error "unexpected argument"
        return 2
        ;;
    esac
  done
  case "$project_ci_log_format" in
    text|json) ;;
    *) project_ci_usage_error "--log-format must be text or json"; return 2 ;;
  esac
  case "$project_ci_log_level" in
    debug|info|warning|error) ;;
    *) project_ci_usage_error "unsupported --log-level"; return 2 ;;
  esac
  case "$project_ci_run_id" in
    "") ;;
    *[!A-Za-z0-9._:-]*) project_ci_usage_error "--run-id is unsafe"; return 2 ;;
  esac
}

# Summary: Secure the optional caller-selected diagnostic file.
# Arguments: none. Environment: selector globals. Stdout: none.
# Stderr: bounded usage diagnostics. Exit classes: zero or usage error 2.
# Side effects: creates or appends one mode-0600 regular file when requested.
# Idempotency: repeated setup preserves the same file. Cleanup: caller-owned.
project_ci_prepare_log_file() {
  local log_dir original_umask
  [ -n "$project_ci_log_file" ] || return 0
  log_dir="${project_ci_log_file%/*}"
  [ "$log_dir" != "$project_ci_log_file" ] || log_dir="."
  if [ ! -d "$log_dir" ]; then
    project_ci_usage_error "--log-file directory does not exist"
    return 2
  fi
  if [ -L "$project_ci_log_file" ]; then
    project_ci_usage_error "--log-file must not be a symlink"
    return 2
  fi
  if [ -e "$project_ci_log_file" ] && [ ! -f "$project_ci_log_file" ]; then
    project_ci_usage_error "--log-file must be a regular file"
    return 2
  fi
  original_umask="$(umask)"
  umask 077
  if ! : >>"$project_ci_log_file"; then
    umask "$original_umask"
    project_ci_usage_error "cannot open --log-file"
    return 2
  fi
  umask "$original_umask"
  if ! chmod 0600 "$project_ci_log_file"; then
    project_ci_usage_error "cannot secure --log-file"
    return 2
  fi
}

# Summary: Emit one bounded, credential-free selector diagnostic.
# Arguments: execution mode and selected result. Environment: selector globals.
# Stdout: none. Stderr: text or JSON Lines diagnostic at info level.
# Exit classes: zero. Side effects: optionally appends to the selected log file.
# Idempotency: one record per call. Cleanup: none.
project_ci_emit_log() {
  local mode="$1" result="$2" timestamp line
  case "$project_ci_log_level" in
    warning|error) return 0 ;;
  esac
  timestamp="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  if [ "$project_ci_log_format" = "json" ]; then
    printf -v line \
      '{"timestamp":"%s","level":"info","run_id":"%s","component":"project-ci-entrypoint","operation":"select","mode":"%s","event":"selection","attempt":1,"resource":"redfish_ctl","result":"%s","elapsed_ms":0,"error_class":null}' \
      "$timestamp" "$project_ci_run_id" "$mode" "$result"
  else
    printf -v line 'INFO: project-ci-entrypoint mode=%s result=%s run_id=%s' \
      "$mode" "$result" "${project_ci_run_id:-none}"
  fi
  printf '%s\n' "$line" >&2
  if [ -n "$project_ci_log_file" ]; then
    printf '%s\n' "$line" >>"$project_ci_log_file"
  fi
}

# Summary: Select exactly one project validation command.
# Arguments: universal non-mutating entrypoint controls. Environment:
# FOCUSED_GATE and PROJECT_CI_PROFILE. Stdout: dry-run result or gate output.
# Stderr: bounded diagnostics. Exit classes: 2 or check.sh.
# Side effects: dry-run is read-only; normal mode delegates to the
# Kubernetes-guarded gate entrypoint. Idempotency and cleanup: inherited from
# the selected gate profile; the optional log file is caller-owned.
project_ci_main() {
  local script_path="${BASH_SOURCE[0]}" script_dir
  local focused_gate="${FOCUSED_GATE:-}"
  local selected_profile="${PROJECT_CI_PROFILE:-}"
  local selected_result="merge"
  local -a args=(--profile merge)

  project_ci_dry_run=false
  project_ci_help=false
  project_ci_log_format="text"
  project_ci_log_level="info"
  project_ci_log_file=""
  project_ci_run_id=""
  project_ci_parse_args "$@" || return "$?"
  if $project_ci_help; then
    project_ci_usage
    return 0
  fi
  project_ci_prepare_log_file || return "$?"

  if [ -n "$focused_gate" ] && [ -n "$selected_profile" ]; then
    project_ci_usage_error \
      "focused gate and profile are mutually exclusive"
    return 2
  fi
  case "$focused_gate" in
    "") ;;
    [A-Za-z0-9]*)
      case "$focused_gate" in
        *[!A-Za-z0-9._-]*)
          project_ci_usage_error "FOCUSED_GATE is unsafe"
          return 2
          ;;
      esac
      ;;
    *) project_ci_usage_error "FOCUSED_GATE is unsafe"; return 2 ;;
  esac
  case "$selected_profile" in
    ""|full) ;;
    focused)
      focused_gate="unit.all"
      ;;
    *)
      project_ci_usage_error "unsupported Builder profile"
      return 2
      ;;
  esac
  if [ -n "$focused_gate" ]; then
    args+=(--gate "$focused_gate")
    selected_result="$focused_gate"
  fi

  if $project_ci_dry_run; then
    project_ci_emit_log "dry-run" "$selected_result"
    if [ "$project_ci_log_format" = "json" ]; then
      printf '{"status":"dry-run","profile":"merge","gate":'
      if [ -n "$focused_gate" ]; then
        printf '"%s"' "$focused_gate"
      else
        printf 'null'
      fi
      printf ',"run_id":"%s"}\n' "$project_ci_run_id"
    else
      printf 'mode=dry-run profile=merge gate=%s\n' \
        "${focused_gate:-none}"
    fi
    return 0
  fi

  project_ci_emit_log "execute" "$selected_result"
  script_dir="${script_path%/*}"
  [ "$script_dir" != "$script_path" ] || script_dir="."
  cd "$script_dir/.."
  exec ./scripts/check.sh "${args[@]}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  project_ci_main "$@"
fi
