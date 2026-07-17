#!/usr/bin/env bash
# Run every gate of a PROFILE from gates.yaml, in registry order; stop on the first failure.
#   run.sh merge | integration | deploy
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
profile="${1:?usage: run.sh <merge|integration|deploy>}"
exec python - "$profile" <<'PY'
import pathlib
import subprocess
import sys

import yaml

profile = sys.argv[1]
registry = yaml.safe_load(pathlib.Path("gates.yaml").read_text(encoding="utf-8"))
gates = [g for g in registry["gates"] if g.get("profile") == profile]
if not gates:
    print(f"run.sh: no gates registered for profile '{profile}'")
    sys.exit(0)
for gate in gates:
    print(f"=== gate {gate['id']} ({gate['command']}) ===")
    if subprocess.run([gate["command"]]).returncode != 0:
        print(f"GATE FAILED: {gate['id']}", file=sys.stderr)
        sys.exit(1)
print(f"run.sh: all {profile} gates passed")
PY
