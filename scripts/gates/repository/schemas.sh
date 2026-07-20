#!/usr/bin/env bash
# repo.schemas (merge): every schema-backed document validates against its JSON schema. Today: the gate
# registry vs schemas/gates.schema.json (jsonschema is a project dep, always available).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
python - <<'PY'
import json, sys, pathlib, yaml, jsonschema
reg = yaml.safe_load(pathlib.Path("gates/manifest.yaml").read_text())
schema = json.loads(pathlib.Path("schemas/gates.schema.json").read_text())
try:
    jsonschema.validate(reg, schema)
except jsonschema.ValidationError as e:
    print(f"repo.schemas: gates/manifest.yaml invalid: {e.message}", file=sys.stderr); sys.exit(1)
print("repo.schemas: OK")
PY
