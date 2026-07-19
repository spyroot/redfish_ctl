#!/usr/bin/env bash
# repo.format (merge): ruff lint over files changed vs origin/main (the tree carries legacy debt, so
# only changed files are gated). Requires ruff in the toolchain.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
if ! command -v ruff >/dev/null 2>&1; then echo "repo.format: ruff not installed" >&2; exit 1; fi
git fetch -q origin main 2>/dev/null || true
# An empty changed-file list is only a legitimate pass when the comparison point actually resolved.
# Without these checks an unreachable origin/main yields an empty list and the gate reports OK having
# linted nothing — a fetch failure must never read as "no Python changed".
if ! base_sha="$(git rev-parse --verify -q origin/main)"; then
  echo "repo.format: origin/main is unresolvable — the changed-file set is unknown" >&2
  exit 1
fi
if ! merge_base="$(git merge-base "$base_sha" HEAD 2>/dev/null)"; then
  # A shallow CI checkout (GitLab's default; .gitlab-ci.yml sets no GIT_DEPTH) truncates BOTH refs, so
  # the merge base can sit below origin/main's shallow boundary even though origin/main itself resolves.
  # Deepen once and retry. This is recovery, not a bypass: if the baseline is still unknowable the gate
  # fails below, so an empty changed set can never come from an unknown baseline.
  git fetch -q --deepen=200 origin main 2>/dev/null || true
  if ! merge_base="$(git merge-base "$base_sha" HEAD 2>/dev/null)"; then
    echo "repo.format: no merge-base with origin/main even after deepening — changed-file set unknown" >&2
    exit 1
  fi
fi
if ! changed="$(git diff --name-only "$merge_base" HEAD -- '*.py')"; then
  echo "repo.format: git diff against the merge base failed" >&2
  exit 1
fi
if [ -n "$changed" ]; then
  printf '%s\n' "$changed" | xargs ruff check
  echo "repo.format: OK ($(printf '%s\n' "$changed" | wc -l | tr -d ' ') changed .py)"
else
  echo "repo.format: OK (origin/main resolved; no .py changed)"
fi
