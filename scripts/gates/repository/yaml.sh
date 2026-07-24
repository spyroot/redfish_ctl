#!/usr/bin/env bash
# repo.yaml (merge): lint YAML. Requires yamllint in the toolchain; falls back to a Python YAML
# syntax parse (always available) so the gate never silently passes.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if command -v yamllint >/dev/null 2>&1; then
  git ls-files '*.yml' '*.yaml' | grep -v '__' | grep -vE 'charts/[^/]+/templates/' | xargs -r yamllint -d relaxed
  echo "repo.yaml: OK (yamllint)"
else
  python - <<'PY'
import re, subprocess, sys, yaml
files = [
    f
    for f in subprocess.check_output(
        ["git", "ls-files", "*.yml", "*.yaml"]
    ).decode().split()
    if "__" not in f and not re.match(r"charts/[^/]+/templates/", f)
]
bad=[]
for f in files:
    try:
        list(yaml.safe_load_all(open(f, encoding="utf-8")))
    except yaml.YAMLError as e:
        bad.append(f"{f}: {e}")
if bad: print("\n".join(bad)); sys.exit(1)
print("repo.yaml: OK (python fallback)")
PY
fi
