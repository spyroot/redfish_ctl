#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf '%s\n' \
    'usage: project-ci-cpu-validation.sh [--dry-run]' \
    '       [--log-format text|json]' \
    '       [--log-level debug|info|warning|error] [--log-file PATH]' \
    '       [--run-id ID]' \
    '' \
    'Audience: both (CI agents and operators).' \
    'Run the CPU validation selected by FOCUSED_GATE or PROJECT_CI_PROFILE.' \
    'PROJECT_CI_PROFILE=focused defaults to the required unit.all gate.'
}

# Return whether one event level passes the configured threshold.
# Arguments: event level and configured level. Stdout/stderr: none.
# Exit classes: 0 enabled, 1 filtered. Side effects and cleanup: none.
project_ci_log_enabled() {
  local event_level="$1"
  local configured_level="$2"
  local event_rank configured_rank

  case "$event_level" in
    debug) event_rank=10 ;;
    info) event_rank=20 ;;
    warning) event_rank=30 ;;
    error) event_rank=40 ;;
    *) return 1 ;;
  esac
  case "$configured_level" in
    debug) configured_rank=10 ;;
    info) configured_rank=20 ;;
    warning) configured_rank=30 ;;
    error) configured_rank=40 ;;
    *) configured_rank=20 ;;
  esac
  (( event_rank >= configured_rank ))
}

# Emit one contract-complete log event.
# Arguments: level, mode, event, result, error class, resource, elapsed ms,
# safe next step, message, and optional blocker authority/observation/risk.
# Environment: parsed PROJECT_CI_* log controls.
# Stdout: none. Stderr: one text or JSONL event. Exit classes: 0 success.
# Side effects: optionally appends to a validated log file. Idempotency: logs
# are append-only. Cleanup: no temporary resources.
project_ci_log_event() {
  local level="$1"
  local mode="$2"
  local event="$3"
  local result="$4"
  local error_class="$5"
  local resource="$6"
  local elapsed_ms="$7"
  local safe_next_step="$8"
  local message="${9:-}"
  local authority_checked="${10:-}"
  local observation="${11:-}"
  local risk="${12:-}"
  local configured_level="${PROJECT_CI_LOG_LEVEL:-info}"
  local format="${PROJECT_CI_LOG_FORMAT:-text}"
  local run_id="${PROJECT_CI_RUN_ID:-}"
  local timestamp record text_format

  project_ci_log_enabled "$level" "$configured_level" || return 0
  timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  if [[ "$format" == "json" ]]; then
    if ! command -v jq >/dev/null 2>&1; then
      printf '%s\n' \
        'BLOCKER: jq is required for machine-readable project CI output' \
        'Authority checked:' \
        '- inventory/ci/smoke-tests.yaml requiredTools' \
        'Observation:' \
        '- jq is unavailable on the selected execution surface' \
        'Risk:' \
        '- JSON output cannot be encoded safely' \
        'Safe next step:' \
        '- run this adapter in the declared Builder resource job' >&2
      return 2
    fi
    record="$(jq -cn \
      --arg timestamp "$timestamp" \
      --arg level "$level" \
      --arg runId "$run_id" \
      --arg mode "$mode" \
      --arg event "$event" \
      --arg resource "$resource" \
      --arg result "$result" \
      --arg errorClass "$error_class" \
      --arg safeNextStep "$safe_next_step" \
      --arg message "$message" \
      --arg authorityChecked "$authority_checked" \
      --arg observation "$observation" \
      --arg risk "$risk" \
      --argjson elapsedMs "$elapsed_ms" \
      '{
        timestamp: $timestamp,
        level: $level,
        runId: $runId,
        component: "project-ci-cpu-validation",
        operation: "validate",
        mode: $mode,
        event: $event,
        attempt: 1,
        resource: $resource,
        result: $result,
        elapsedMs: $elapsedMs,
        errorClass: $errorClass,
        safeNextStep: $safeNextStep,
        message: $message
      }
      + (if $authorityChecked == "" then {} else {
          authorityChecked: $authorityChecked,
          observation: $observation,
          risk: $risk
        } end)')"
  else
    printf -v text_format '%s' \
      'level=%s run_id=%s component=project-ci-cpu-validation' \
      ' operation=validate mode=%s event=%s attempt=1 resource=%s' \
      ' result=%s elapsed_ms=%s error_class=%s'
    printf -v record "$text_format" \
      "$level" "$run_id" "$mode" "$event" "$resource" "$result" \
      "$elapsed_ms" "$error_class"
    if [[ -n "$message" ]]; then
      record+=" message=$message"
    fi
    if [[ -n "$safe_next_step" ]]; then
      record+=" safe_next_step=$safe_next_step"
    fi
    if [[ -n "$authority_checked" ]]; then
      record+=" authority_checked=$authority_checked"
      record+=" observation=$observation risk=$risk"
    fi
  fi
  printf '%s\n' "$record" >&2
  if [[ "${PROJECT_CI_LOG_FILE_READY:-false}" == true &&
        -n "${PROJECT_CI_LOG_FILE:-}" ]]; then
    ( umask 077; printf '%s\n' "$record" >>"$PROJECT_CI_LOG_FILE" )
  fi
}

# Emit one mandatory structured blocker.
# Arguments: message, safe next step, optional error class, authority, and risk.
# Environment: parsed PROJECT_CI_* log controls. Stdout: none. Stderr: blocker.
# Exit classes: always 2. Side effects: optional validated log append.
# Idempotency: deterministic apart from timestamp. Cleanup: none.
project_ci_blocker() {
  local message="$1"
  local safe_next_step="$2"
  local error_class="${3:-usage-error}"
  local authority_checked="${4:-standards-binding.yaml and pinned automation contracts}"
  local risk="${5:-required validation could be selected or reported incorrectly}"
  local run_id="${PROJECT_CI_RUN_ID:-}"

  if [[ "${PROJECT_CI_LOG_FORMAT:-text}" == "json" ]]; then
    if [[ ! "$run_id" =~ ^[A-Za-z0-9._:-]{1,128}$ ]]; then
      PROJECT_CI_RUN_ID=invalid
    fi
    project_ci_log_event error selection blocker blocked "$error_class" \
      selection 0 "$safe_next_step" "$message" "$authority_checked" \
      "$message" "$risk"
  else
    printf 'BLOCKER: %s\n' "$message" >&2
    printf 'Authority checked:\n- %s\n' "$authority_checked" >&2
    printf 'Observation:\n- %s\n' "$message" >&2
    printf 'Risk:\n- %s\n' "$risk" >&2
    printf 'Safe next step:\n- %s\n' "$safe_next_step" >&2
  fi
  return 2
}

# Resolve Builder's one CPU resource job to exactly one consumer gate mode.
# Arguments: focused gate, project CI profile, and optional smoke selector.
# Stdout: focused|full|smoke.
# Exit classes: 0 valid selection, 2 invalid or ambiguous dispatch.
# Side effects: none. Idempotency: deterministic for the same inputs.
project_ci_cpu_mode() {
  local focused_gate="$1"
  local project_profile="$2"
  local project_smoke="${3:-}"

  if [[ -n "$project_smoke" ]]; then
    if [[ -n "$focused_gate" || -n "$project_profile" ]]; then
      project_ci_blocker "PROJECT_CI_SMOKE cannot be combined with another selector" \
        "use exactly one project-ci selector"
      return 2
    fi
    if [[ "$project_smoke" != "project-ci-cpu-validation" ]]; then
      project_ci_blocker "unsupported PROJECT_CI_SMOKE selection" \
        "select project-ci-cpu-validation from the smoke inventory"
      return 2
    fi
    printf 'smoke\n'
    return 0
  fi

  if [[ -n "$focused_gate" ]]; then
    if [[ ${#focused_gate} -gt 128 ||
          ! "$focused_gate" =~ ^[a-z0-9]+([._-][a-z0-9]+)*$ ]]; then
      project_ci_blocker "FOCUSED_GATE must be a bounded gate identifier" \
        "select a gate id from ./scripts/check.sh --list"
      return 2
    fi
    if [[ -n "$project_profile" && "$project_profile" != "focused" ]]; then
      project_ci_blocker \
        "FOCUSED_GATE cannot be combined with PROJECT_CI_PROFILE=$project_profile" \
        "use FOCUSED_GATE alone or PROJECT_CI_PROFILE=focused"
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
      printf 'focused\n'
      ;;
    *)
      project_ci_blocker "PROJECT_CI_PROFILE must be focused or full" \
        "set PROJECT_CI_PROFILE=focused or PROJECT_CI_PROFILE=full"
      return 2
      ;;
  esac
}

# Resolve the focused gate after the mode has been validated.
# Arguments: focused gate and project CI profile. Stdout: gate id or empty.
# Exit classes: 0 valid selection, 2 invalid selection. Side effects: none.
project_ci_cpu_gate() {
  local focused_gate="$1"
  local project_profile="$2"
  local project_smoke="${3:-}"
  local mode

  mode="$(project_ci_cpu_mode \
    "$focused_gate" "$project_profile" "$project_smoke")" || return "$?"
  if [[ "$mode" != "focused" ]]; then
    return 0
  fi
  printf '%s\n' "${focused_gate:-unit.all}"
}

# Validate an explicitly requested append-only log target without opening it.
# Arguments: path. Stdout: none. Stderr: classified blocker on failure.
# Exit classes: 0 safe target, 2 unsafe target. Side effects and cleanup: none.
project_ci_validate_log_target() {
  local log_file="$1"

  if [[ -z "$log_file" ]]; then
    PROJECT_CI_LOG_FILE_READY=true
    return 0
  fi
  if [[ "$log_file" == *$'\n'* || "$log_file" == *$'\r'* ||
        -L "$log_file" || ( -e "$log_file" && ! -f "$log_file" ) ]]; then
    project_ci_blocker "--log-file must name a regular non-symlink file" \
      "select a regular file in an existing writable directory" log-target-invalid
    return 2
  fi
  if [[ ! -d "$(dirname "$log_file")" ]]; then
    project_ci_blocker "--log-file parent directory does not exist" \
      "create the intended directory outside this tool or omit --log-file" \
      log-target-invalid
    return 2
  fi
  PROJECT_CI_LOG_FILE_READY=true
}

# Parse the universal non-interactive CLI controls into PROJECT_CI_* globals.
# Arguments: CLI options. Stdout: none. Exit classes: 0 valid, 2 invalid.
# Side effects: updates function-scoped process globals only.
project_ci_parse_args() {
  PROJECT_CI_DRY_RUN=false
  PROJECT_CI_HELP=false
  PROJECT_CI_LOG_FORMAT=text
  PROJECT_CI_LOG_LEVEL=info
  PROJECT_CI_LOG_FILE=""
  PROJECT_CI_LOG_FILE_READY=false
  PROJECT_CI_RUN_ID="${CI_PIPELINE_ID:-not-provided}"

  while (( $# > 0 )); do
    case "$1" in
      --help|-h)
        PROJECT_CI_HELP=true
        shift
        ;;
      --dry-run)
        PROJECT_CI_DRY_RUN=true
        shift
        ;;
      --log-format|--log-level|--log-file|--run-id)
        if (( $# < 2 )); then
          project_ci_blocker "$1 requires a value" \
            "rerun with $1 followed by its value"
          return 2
        fi
        case "$1" in
          --log-format) PROJECT_CI_LOG_FORMAT="$2" ;;
          --log-level) PROJECT_CI_LOG_LEVEL="$2" ;;
          --log-file) PROJECT_CI_LOG_FILE="$2" ;;
          --run-id) PROJECT_CI_RUN_ID="$2" ;;
        esac
        shift 2
        ;;
      *)
        project_ci_blocker "unknown option: $1" \
          "run project-ci-cpu-validation.sh --help"
        return 2
        ;;
    esac
  done

  case "$PROJECT_CI_LOG_FORMAT" in
    text|json) ;;
    *)
      project_ci_blocker "--log-format must be text or json" \
        "select --log-format text or --log-format json"
      return 2
      ;;
  esac
  case "$PROJECT_CI_LOG_LEVEL" in
    debug|info|warning|error) ;;
    *)
      project_ci_blocker \
        "--log-level must be debug, info, warning, or error" \
        "select a supported --log-level"
      return 2
      ;;
  esac
  if [[ -n "$PROJECT_CI_RUN_ID" &&
        ! "$PROJECT_CI_RUN_ID" =~ ^[A-Za-z0-9._:-]{1,128}$ ]]; then
    project_ci_blocker "--run-id must be a bounded identifier" \
      "use 1-128 letters, digits, dots, underscores, colons, or hyphens"
    return 2
  fi
  if [[ "$PROJECT_CI_DRY_RUN" == true && -n "$PROJECT_CI_LOG_FILE" ]]; then
    project_ci_blocker "--log-file cannot be combined with --dry-run" \
      "remove --log-file for a side-effect-free plan"
    return 2
  fi
  project_ci_validate_log_target "$PROJECT_CI_LOG_FILE" || return "$?"
}

# Render one bounded selection record.
# Arguments: format, mode, gate, run id. Stdout: one text or JSON line.
# Exit classes: 0. Side effects: none.
project_ci_selection_record() {
  local format="$1"
  local mode="$2"
  local gate="$3"
  local run_id="$4"
  local record

  if [[ "$format" == "json" ]]; then
    record="$(jq -cn \
      --arg mode "$mode" \
      --arg gate "$gate" \
      --arg runId "$run_id" \
      '{
        mode: $mode,
        gate: $gate,
        mutation: false,
        runId: $runId,
        status: "planned",
        warningCount: 0,
        cleanupStatus: "not-required",
        evidencePath: ""
      }')"
    printf '%s\n' "$record"
  else
    printf -v record '%s' \
      "mode=$mode gate=$gate mutation=false run_id=$run_id status=planned" \
      ' warning_count=0 cleanup_status=not-required evidence_path='
    printf '%s\n' "$record"
  fi
}

# Emit an informational selection record through the configured logger.
# Arguments: mode, gate, and elapsed milliseconds. Stdout: none.
# Exit classes: 0. Side effects: optional validated log append. Cleanup: none.
project_ci_log_selection() {
  local mode="$1"
  local gate="$2"
  local elapsed_ms="$3"
  local resource="profile:merge"

  if [[ -n "$gate" ]]; then
    resource="gate:$gate"
  fi
  project_ci_log_event info "$mode" selection running none \
    "$resource" "$elapsed_ms" "" "selected project CI validation"
}

# Render one terminal adapter result. Builder owns canonical smoke evidence;
# this consumer reports only the path to the gate evidence it observed.
# Arguments: format, mode, gate, run id, status, warning count, cleanup status,
# evidence path, and error class. Stdout: one bounded text or JSON result.
# Exit classes: 0. Side effects and cleanup: none.
project_ci_terminal_result() {
  local format="$1"
  local mode="$2"
  local gate="$3"
  local run_id="$4"
  local status="$5"
  local warning_count="$6"
  local cleanup_status="$7"
  local evidence_path="$8"
  local error_class="$9"
  local result

  if [[ "$format" == "json" ]]; then
    result="$(jq -cn \
      --arg mode "$mode" \
      --arg gate "$gate" \
      --arg runId "$run_id" \
      --arg status "$status" \
      --arg cleanupStatus "$cleanup_status" \
      --arg evidencePath "$evidence_path" \
      --arg errorClass "$error_class" \
      --argjson warningCount "$warning_count" \
      '{
        mode: $mode,
        gate: $gate,
        mutation: false,
        runId: $runId,
        status: $status,
        warningCount: $warningCount,
        cleanupStatus: $cleanupStatus,
        evidencePath: $evidencePath,
        errorClass: $errorClass
      }')"
    printf '%s\n' "$result"
  else
    printf '%s%s%s\n' \
      "mode=$mode gate=$gate mutation=false run_id=$run_id status=$status" \
      " warning_count=$warning_count cleanup_status=$cleanup_status" \
      " evidence_path=$evidence_path error_class=$error_class"
  fi
}

# Count captured dependency stderr classes without replaying raw content.
# Arguments: capture path. Environment inputs: none. Stdout: tab-separated
# total/auth/network/warning/error/other counts. Stderr: none. Exit classes: 0.
# Side effects and cleanup: none.
project_ci_stderr_counts() {
  local stderr_file="$1"

  awk '
    BEGIN { auth = network = warning = error = other = total = 0 }
    {
      text = tolower($0)
      total++
      if (text ~ /(unauthorized|forbidden|credential|password|token|401|403)/) {
        auth++
      } else if (text ~ /(timeout|connection|network|dns|unreachable|reset)/) {
        network++
      } else if (text ~ /(error|failed|failure|fatal)/) {
        error++
      } else if (
        text ~ /^time=[^[:space:]]+ level=warn msg="/ &&
        text ~ /--yaml-fix-merge-anchor-to-spec is false;/ &&
        text ~ /this flag will default to true in late 2025\."$/
      ) {
        warning++
      } else {
        other++
      }
    }
    END {
      printf "%d\t%d\t%d\t%d\t%d\t%d\n", \
        total, auth, network, warning, error, other
    }
  ' "$stderr_file"
}

# Render one sanitized stderr classification from the shared count helper.
# Arguments: capture path. Environment inputs: none. Stdout: one summary.
# Stderr: none. Exit classes: 0. Side effects and cleanup: none.
project_ci_stderr_summary() {
  local stderr_file="$1"
  local total auth network warning error other

  IFS=$'\t' read -r total auth network warning error other \
    < <(project_ci_stderr_counts "$stderr_file")
  printf 'stderr-lines=%d classes=auth:%d,network:%d,' \
    "$total" "$auth" "$network"
  printf 'warning:%d,error:%d,other:%d redaction=full' \
    "$warning" "$error" "$other"
}

# Execute one selected gate invocation without replaying dependency stderr.
# Arguments: check script, mode, gate, and start time. Environment: configured
# logger. Stdout: dependency stdout followed by one terminal result. Stderr:
# sanitized classified events only. Exit classes: dependency status, or 2 when
# owned temporary cleanup fails. Side effects: runs the selected read-only gate.
# Idempotency: delegated to the gate. Cleanup: always removes owned stderr file.
project_ci_run_gate() {
  local check_script="$1"
  local mode="$2"
  local gate="$3"
  local start_seconds="$4"
  local evidence_path=""
  local stderr_file="" cleanup_status=passed
  local elapsed_ms status=0 stderr_lines=0 warning_count=0
  local auth_lines=0 network_lines=0 warning_lines=0 error_lines=0 other_lines=0
  local diagnostic_summary="stderr-lines=0 redaction=full"
  local result_status=passed error_class=none event_level=info
  local resource=profile:merge
  local child_pid="" child_running=false
  local saved_exit_trap saved_hup_trap saved_int_trap saved_term_trap
  local -a gate_args=(--profile merge)

  saved_exit_trap="$(trap -p EXIT)"
  saved_hup_trap="$(trap -p HUP)"
  saved_int_trap="$(trap -p INT)"
  saved_term_trap="$(trap -p TERM)"

  # Restore the caller's trap ownership after normal completion.
  # Arguments: none. Environment: captured caller traps. Stdout/stderr: none.
  # Exit classes: 0. Side effects: restores signal/exit handlers.
  # Idempotency: repeat-safe. Cleanup: releases this function's trap ownership.
  restore_gate_traps() {
    local saved_trap

    trap - EXIT HUP INT TERM
    for saved_trap in \
      "$saved_exit_trap" "$saved_hup_trap" "$saved_int_trap" "$saved_term_trap"; do
      if [[ -n "$saved_trap" ]]; then
        eval "$saved_trap"
      fi
    done
  }

  # Remove the function-owned stderr capture and log the action.
  # Arguments: none. Environment: closure state. Stdout: none. Stderr: log.
  # Exit classes: 0 removed/absent, 1 removal failed. Side effects: removes one
  # owned temporary file. Idempotency: safe twice. Cleanup: this is cleanup.
  cleanup_gate_stderr() {
    local cleanup_result=passed cleanup_error=none
    local cleanup_level=info

    if [[ -z "$stderr_file" || ! -e "$stderr_file" ]]; then
      return 0
    fi
    if ! rm -f -- "$stderr_file"; then
      cleanup_result=failed
      cleanup_error=cleanup-failed
      cleanup_level=error
      cleanup_status=failed
    else
      stderr_file=""
    fi
    project_ci_log_event \
      "$cleanup_level" "$mode" cleanup "$cleanup_result" \
      "$cleanup_error" dependency-stderr 0 \
      "rerun in a clean Builder resource job when cleanup fails" \
      "removed dependency stderr capture"
    [[ "$cleanup_result" == "passed" ]]
  }

  # Terminate and reap the active gate process group during cancellation.
  # Arguments: none. Environment: closure state. Stdout: none. Stderr: log.
  # Exit classes: 0 stopped/absent, 1 stop unproven. Side effects: terminates
  # only the tracked process group. Idempotency: safe twice. Cleanup: reaps it.
  stop_gate_process() {
    local termination_result=passed termination_error=none
    local termination_level=info
    local wait_status=0

    if [[ "$child_running" != true || -z "$child_pid" ]]; then
      return 0
    fi
    if kill -TERM -- "-$child_pid" 2>>"$stderr_file"; then
      :
    elif kill -TERM "$child_pid" 2>>"$stderr_file"; then
      :
    else
      termination_result=failed
      termination_error=cleanup-failed
      termination_level=error
    fi
    if wait "$child_pid" 2>>"$stderr_file"; then
      wait_status=0
    else
      wait_status=$?
    fi
    child_running=false
    child_pid=""
    if (( wait_status == 127 )); then
      termination_result=failed
      termination_error=cleanup-failed
      termination_level=error
    fi
    project_ci_log_event \
      "$termination_level" "$mode" cleanup \
      "$termination_result" "$termination_error" gate-process 0 \
      "inspect Builder job cancellation and retry the exact commit" \
      "terminated and reaped active gate process group"
    [[ "$termination_result" == "passed" ]]
  }

  # Clean every resource owned by the active gate invocation.
  # Arguments: none. Environment: closure state. Stdout: none. Stderr: logs.
  # Exit classes: 0 cleaned, 1 cleanup unproven. Side effects: may terminate the
  # tracked process group and remove one temp file. Idempotency: safe twice.
  # Cleanup: this is the aggregate cleanup action.
  cleanup_gate_runtime() {
    local runtime_cleanup=passed

    if ! stop_gate_process; then
      runtime_cleanup=failed
    fi
    if ! cleanup_gate_stderr; then
      runtime_cleanup=failed
    fi
    [[ "$runtime_cleanup" == "passed" ]]
  }

  # Convert a received signal into cleanup and a terminal failed result.
  # Arguments: signal name and exit code. Environment: closure state.
  # Stdout: terminal result. Stderr: cleanup/signal logs. Exit: signal class.
  # Side effects: terminates the tracked gate. Idempotency: one-shot handler.
  # Cleanup: process group and owned stderr capture are cleaned before exit.
  handle_gate_signal() {
    local signal_name="$1"
    local signal_exit="$2"
    local process_cleanup=passed signal_error

    stop_gate_process || process_cleanup=failed
    if [[ -n "$stderr_file" && -f "$stderr_file" ]]; then
      diagnostic_summary="$(project_ci_stderr_summary "$stderr_file")"
      stderr_lines="$(awk 'END { print NR + 0 }' "$stderr_file")"
    fi
    cleanup_gate_stderr || cleanup_status=failed
    if [[ "$process_cleanup" != "passed" ]]; then
      cleanup_status=failed
    fi
    if (( stderr_lines > 0 )); then
      warning_count=1
    fi
    signal_error="signal-${signal_name,,}"
    elapsed_ms=$(( (SECONDS - start_seconds) * 1000 ))
    project_ci_log_event error "$mode" signal failed "$signal_error" \
      "$resource" "$elapsed_ms" \
      "rerun the exact commit after confirming the cancellation cause" \
      "$diagnostic_summary"
    project_ci_terminal_result \
      "$PROJECT_CI_LOG_FORMAT" "$mode" "$gate" "$PROJECT_CI_RUN_ID" \
      failed "$warning_count" "$cleanup_status" "" "$signal_error"
    restore_gate_traps
    exit "$signal_exit"
  }

  if [[ "$mode" == "focused" ]]; then
    gate_args+=(--gate "$gate")
    resource="gate:$gate"
  fi
  stderr_file="$(mktemp "${TMPDIR:-/tmp}/project-ci-cpu-validation.XXXXXX")" || {
    project_ci_blocker "cannot allocate dependency stderr capture" \
      "rerun in a Builder resource job with a writable temporary directory" \
      temp-allocation-failed
    return 2
  }
  trap cleanup_gate_runtime EXIT
  trap 'handle_gate_signal HUP 129' HUP
  trap 'handle_gate_signal INT 130' INT
  trap 'handle_gate_signal TERM 143' TERM

  setsid "$check_script" "${gate_args[@]}" 2>"$stderr_file" &
  child_pid=$!
  child_running=true
  if wait "$child_pid"; then
    status=0
  else
    status=$?
  fi
  child_running=false
  child_pid=""
  diagnostic_summary="$(project_ci_stderr_summary "$stderr_file")"
  IFS=$'\t' read -r \
    stderr_lines auth_lines network_lines warning_lines error_lines other_lines \
    < <(project_ci_stderr_counts "$stderr_file")
  if (( stderr_lines > 0 )); then
    warning_count=1
  fi
  cleanup_gate_stderr || cleanup_status=failed

  if (( status != 0 )); then
    result_status=failed
    error_class="gate-failed"
    event_level=error
  elif (( auth_lines + network_lines + error_lines + other_lines > 0 )); then
    status=2
    result_status=failed
    error_class="dependency-stderr"
    event_level=error
  elif [[ "$cleanup_status" != "passed" ]]; then
    status=2
    result_status=failed
    error_class="cleanup-failed"
    event_level=error
  elif (( warning_lines > 0 )); then
    event_level=warning
  fi

  elapsed_ms=$(( (SECONDS - start_seconds) * 1000 ))
  project_ci_log_event "$event_level" "$mode" terminal "$result_status" \
    "$error_class" "$resource" "$elapsed_ms" \
    "inspect Builder canonical evidence for the exact commit" \
    "$diagnostic_summary"
  project_ci_terminal_result \
    "$PROJECT_CI_LOG_FORMAT" "$mode" "$gate" "$PROJECT_CI_RUN_ID" \
    "$result_status" "$warning_count" "$cleanup_status" "$evidence_path" \
    "$error_class"
  restore_gate_traps
  return "$status"
}

main() {
  local focused_gate="${FOCUSED_GATE:-}"
  local project_profile="${PROJECT_CI_PROFILE:-}"
  local project_smoke="${PROJECT_CI_SMOKE:-}"
  local mode gate elapsed_ms
  local start_seconds="$SECONDS"

  project_ci_parse_args "$@" || return "$?"
  if [[ "$PROJECT_CI_HELP" == true ]]; then
    usage
    return 0
  fi

  mode="$(project_ci_cpu_mode \
    "$focused_gate" "$project_profile" "$project_smoke")" || return "$?"
  gate="$(project_ci_cpu_gate \
    "$focused_gate" "$project_profile" "$project_smoke")" || return "$?"
  if [[ "$PROJECT_CI_DRY_RUN" == true ]]; then
    project_ci_selection_record \
      "$PROJECT_CI_LOG_FORMAT" "$mode" "$gate" "$PROJECT_CI_RUN_ID"
    return 0
  fi
  elapsed_ms=$(( (SECONDS - start_seconds) * 1000 ))
  project_ci_log_selection "$mode" "$gate" "$elapsed_ms" || return "$?"
  case "$mode" in
    focused|full|smoke)
      project_ci_run_gate \
        ./scripts/check.sh "$mode" "$gate" "$start_seconds"
      ;;
    *)
      project_ci_blocker "unsupported CPU validation mode: $mode" \
        "select focused or full validation"
      return 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
