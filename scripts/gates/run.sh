#!/usr/bin/env bash
# Internal backend for scripts/check.sh. Run a profile, or one gate in a profile,
# from gates/manifest.yaml. An unknown profile, unknown gate, or profile/gate
# mismatch is an error, never a silent pass.
#   run.sh merge | integration | scheduled | deploy | repository-export
#   run.sh --profile merge [--gate unit.all]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

usage() {
  cat <<'USAGE'
usage: run.sh <merge|integration|scheduled|deploy|repository-export>
       run.sh --profile <merge|integration|scheduled|deploy|repository-export> [--gate <id>]
USAGE
}

profile=""
gate=""
if [ "$#" -gt 0 ] && [[ "$1" != -* ]]; then
  profile="$1"
  shift
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      profile="${2:?run.sh: --profile requires a value}"
      shift 2
      ;;
    --gate)
      gate="${2:?run.sh: --gate requires a value}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "run.sh: unexpected argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$profile" ]; then
  echo "run.sh: a profile is required" >&2
  usage >&2
  exit 2
fi

exec python3 - "$profile" "$gate" <<'PY'
import os
import pathlib
import re
import sys
import time

import yaml

from tools.ci_evidence import (
    EvidenceError,
    build_evidence,
    build_smoke_evidence,
    gate_timeout_seconds,
    observe_gate,
    observe_smoke,
    select_release_blocking_smoke,
    smoke_timeout_seconds,
    write_evidence,
)

profile = sys.argv[1]
selected_gate = sys.argv[2] or None
safe_gate = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", selected_gate or "")
if selected_gate and not safe_gate:
    print("run.sh: unsafe gate identifier", file=sys.stderr)
    sys.exit(2)
registry = yaml.safe_load(pathlib.Path("gates/manifest.yaml").read_text(encoding="utf-8"))
provider_gates = {
    record.get("id"): record
    for record in registry.get("spec", {}).get("gates", [])
    if isinstance(record, dict) and record.get("id")
}
known = sorted({g.get("profile") for g in registry["gates"] if g.get("profile")})
if profile not in known:
    print(f"run.sh: unknown profile '{profile}' — registered profiles: {', '.join(known)}", file=sys.stderr)
    sys.exit(1)
gates = [g for g in registry["gates"] if g.get("profile") == profile]
if not gates:
    print(f"run.sh: profile '{profile}' has no registered gates", file=sys.stderr)
    sys.exit(1)
if selected_gate:
    matches = [gate for gate in registry["gates"] if gate.get("id") == selected_gate]
    if not matches:
        print(f"run.sh: unknown gate '{selected_gate}'", file=sys.stderr)
        sys.exit(1)
    selected = matches[0]
    if selected.get("profile") != profile:
        print(
            f"run.sh: gate '{selected_gate}' belongs to profile "
            f"'{selected.get('profile')}', not '{profile}'",
            file=sys.stderr,
        )
        sys.exit(1)
    gates = [selected]

job_name = os.environ.get("CI_JOB_NAME", "")
smoke_inventory = yaml.safe_load(
    pathlib.Path("inventory/ci/smoke-tests.yaml").read_text(encoding="utf-8")
)
try:
    smoke_record = select_release_blocking_smoke(
        smoke_inventory,
        job_name=job_name,
        selected_gate=selected_gate,
    )
except EvidenceError as exc:
    print(f"EVIDENCE FAILED: {exc}", file=sys.stderr)
    sys.exit(1)
if selected_gate:
    print(
        "run.sh: focused execution omits release-blocking smoke evidence "
        f"for {job_name}"
    )
try:
    if smoke_record:
        profile_timeout_seconds = smoke_timeout_seconds(smoke_record)
        timeout_source = "inventory timeoutSeconds applied across registered gates"
    else:
        profile_timeout_seconds = 3600
        timeout_source = "runner default: no smoke inventory record for this job"
except EvidenceError as exc:
    print(f"EVIDENCE FAILED: {exc}", file=sys.stderr)
    sys.exit(1)
profile_deadline = time.monotonic() + profile_timeout_seconds
pathlib.Path("reports").mkdir(parents=True, exist_ok=True)
run_status = "passed"
run_return_code = 0
job_observations = {
    "return_code": 0,
    "timed_out": False,
    "timeout_seconds": profile_timeout_seconds,
    "applied_timeout_seconds": [],
    "warnings": 0,
    "output_sanitized": True,
    "skipped_required_tests": 0,
    "skipped_optional_tests": 0,
    "cleanup_status": "passed",
    "remaining": [],
    "sources": {
        "warnings": "captured output from every executed gate",
        "skips": "pytest -ra reasons from every executed gate",
        "timeout": timeout_source,
        "cleanup": "per-gate git status tracked and untracked-state comparisons",
        "sanitization": (
            "captured gate output scanned before bounded publication and "
            "evidence scanned before atomic write"
        ),
    },
}
for gate in gates:
    print(f"=== gate {gate['id']} ({gate['command']}) ===")
    remaining_seconds = max(profile_deadline - time.monotonic(), 0.001)
    provider_gate = provider_gates.get(gate["id"])
    if provider_gate is None:
        print(
            f"EVIDENCE FAILED: provider gate metadata missing for {gate['id']}",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        selected_timeout_seconds = gate_timeout_seconds(provider_gate)
    except EvidenceError as exc:
        print(f"EVIDENCE FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    applied_timeout_seconds = min(remaining_seconds, selected_timeout_seconds)
    observations = observe_gate(
        command=gate["command"],
        timeout_seconds=applied_timeout_seconds,
    )
    job_observations["applied_timeout_seconds"].append(
        observations["timeout_seconds"]
    )
    return_code = observations["return_code"]
    status = "passed" if return_code == 0 else "failed"
    job_observations["warnings"] += observations["warnings"]
    if not observations["output_sanitized"]:
        job_observations["output_sanitized"] = False
    job_observations["skipped_required_tests"] += observations[
        "skipped_required_tests"
    ]
    job_observations["skipped_optional_tests"] += observations[
        "skipped_optional_tests"
    ]
    if observations["timed_out"]:
        job_observations["timed_out"] = True
    if observations["cleanup_status"] != "passed":
        job_observations["cleanup_status"] = "failed"
    job_observations["remaining"].extend(observations["remaining"])
    try:
        evidence = build_evidence(
            kind="gate",
            name=gate["id"],
            command=gate["command"],
            status=status,
            return_code=return_code,
            observations=observations,
        )
        written = write_evidence(
            pathlib.Path("reports/gates") / f"{gate['id']}.json",
            evidence,
        )
    except (EvidenceError, OSError, ValueError) as exc:
        print(f"EVIDENCE FAILED: {gate['id']}: {exc}", file=sys.stderr)
        run_status = "failed"
        run_return_code = 1
        break
    if written["status"] != "passed":
        print(f"GATE FAILED: {gate['id']}", file=sys.stderr)
        run_status = "failed"
        run_return_code = written["return_code"] or 1
        break

job_observations["return_code"] = run_return_code
if time.monotonic() > profile_deadline:
    job_observations["timed_out"] = True
    run_status = "failed"
    run_return_code = run_return_code or 124
    job_observations["return_code"] = run_return_code

if smoke_record:
    try:
        smoke_observations = observe_smoke(
            record=smoke_record,
            gate_observations=job_observations,
        )
        smoke_evidence = build_smoke_evidence(
            record=smoke_record,
            gate_observations=job_observations,
            smoke_observations=smoke_observations,
        )
        written_smoke = write_evidence(
            pathlib.Path("reports/smoke") / f"{job_name}.json",
            smoke_evidence,
        )
        if written_smoke["status"] != "passed":
            run_status = "failed"
            run_return_code = written_smoke["return_code"] or 1
    except (EvidenceError, OSError, ValueError) as exc:
        print(f"EVIDENCE FAILED: smoke result: {exc}", file=sys.stderr)
        run_status = "failed"
        run_return_code = 1

job_command = f"./scripts/check.sh --profile {profile}"
if selected_gate:
    job_command += f" --gate {selected_gate}"
try:
    job_evidence = build_evidence(
        kind="job",
        name=job_name,
        command=job_command,
        status=run_status,
        return_code=run_return_code,
        observations=job_observations,
    )
    written_job = write_evidence(
        pathlib.Path("reports/ci") / f"{job_name}.json",
        job_evidence,
    )
    if written_job["status"] != "passed":
        run_status = "failed"
        run_return_code = written_job["return_code"] or 1
except (EvidenceError, OSError, ValueError) as exc:
    print(f"EVIDENCE FAILED: CI job: {exc}", file=sys.stderr)
    sys.exit(1)

if run_return_code != 0:
    sys.exit(run_return_code)
if selected_gate:
    print(f"run.sh: gate {selected_gate} passed")
else:
    print(f"run.sh: all {profile} gates passed")
PY
