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
import pathlib
import subprocess
import sys

import yaml

profile = sys.argv[1]
selected_gate = sys.argv[2] or None
registry = yaml.safe_load(pathlib.Path("gates/manifest.yaml").read_text(encoding="utf-8"))
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
for gate in gates:
    print(f"=== gate {gate['id']} ({gate['command']}) ===")
    if subprocess.run([gate["command"]]).returncode != 0:
        print(f"GATE FAILED: {gate['id']}", file=sys.stderr)
        sys.exit(1)
if selected_gate:
    print(f"run.sh: gate {selected_gate} passed")
else:
    print(f"run.sh: all {profile} gates passed")
PY
