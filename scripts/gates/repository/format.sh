#!/usr/bin/env bash
# repo.format (merge): ruff lint over files changed vs origin/main (the tree carries legacy debt, so
# only changed files are gated). Requires ruff in the toolchain.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v ruff >/dev/null 2>&1; then echo "repo.format: ruff not installed" >&2; exit 1; fi
git fetch -q origin main 2>/dev/null || true
changed="$(git diff --name-only origin/main...HEAD -- '*.py' 2>/dev/null || true)"
if [ -n "$changed" ]; then printf '%s\n' "$changed" | xargs ruff check; else echo "repo.format: no changed .py"; fi
echo "repo.format: OK"
