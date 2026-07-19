#!/usr/bin/env bash
# Run every gate of a PROFILE from the gate registry (gates/manifest.yaml), in registry order; stop on
# the first failure. An unknown profile is an error, never a silent pass.
#   run.sh merge | integration | deploy
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
profile="${1:?usage: run.sh <merge|integration|deploy>}"
exec python3 - "$profile" <<'PY'
import pathlib
import subprocess
import sys

import yaml

profile = sys.argv[1]
registry = yaml.safe_load(pathlib.Path("gates/manifest.yaml").read_text(encoding="utf-8"))
known = sorted({g.get("profile") for g in registry["gates"] if g.get("profile")})
if profile not in known:
    print(f"run.sh: unknown profile '{profile}' — registered profiles: {', '.join(known)}", file=sys.stderr)
    sys.exit(1)
gates = [g for g in registry["gates"] if g.get("profile") == profile]
if not gates:
    print(f"run.sh: profile '{profile}' has no registered gates", file=sys.stderr)
    sys.exit(1)
for gate in gates:
    print(f"=== gate {gate['id']} ({gate['command']}) ===")
    if subprocess.run([gate["command"]]).returncode != 0:
        print(f"GATE FAILED: {gate['id']}", file=sys.stderr)
        sys.exit(1)
print(f"run.sh: all {profile} gates passed")
PY
