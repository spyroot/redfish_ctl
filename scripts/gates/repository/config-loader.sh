#!/usr/bin/env bash
# repo.config-loader (merge, mutates:false): the environment is read in ONE
# loader (redfish_ctl/config.py) — a raw os.getenv/os.environ/env_first read
# anywhere else fails. Stronger than name-scanning: it forces centralization.
# Implementation: tools/config_loader_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/config_loader_gate.py
