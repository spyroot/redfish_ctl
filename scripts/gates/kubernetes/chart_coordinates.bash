#!/usr/bin/env bash
# Derive static Helm render coordinates from chart metadata.

# Summary: Read and validate a Helm chart name from Chart.yaml.
# Arguments: chart directory. Stdout: chart name.
# Exit classes: Python/YAML status. Side effects: none.
# Idempotency: deterministic for one Chart.yaml. Cleanup: none.
helm_chart_name() {
    local chart_dir="$1"
    python - "$chart_dir/Chart.yaml" <<'PY'
import re
import sys
from pathlib import Path

import yaml

document = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
name = document.get("name", "") if isinstance(document, dict) else ""
if not isinstance(name, str) or not re.fullmatch(
    r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", name
):
    raise SystemExit("Chart.yaml name is not a DNS-1123 label")
print(name)
PY
}

# Summary: Derive a non-live namespace for static chart validation.
# Arguments: chart name. Stdout: render-only namespace.
# Exit classes: 0 valid, 1 too long. Side effects: none.
# Idempotency: deterministic. Cleanup: none.
helm_render_namespace() {
    local chart_name="$1"
    local namespace="${chart_name}-render"
    [ "${#namespace}" -le 63 ] || return 1
    printf '%s\n' "$namespace"
}
