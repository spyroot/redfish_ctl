#!/usr/bin/env bash
# repo.docstring (merge, mutates:false): whole-tree reST docstring gate.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python tools/docstring_gate.py --all
