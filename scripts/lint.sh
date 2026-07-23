#!/usr/bin/env bash
# lint.sh -- the single LOCAL lint entry for agents and humans.
#
# Mirrors the CI merge profile's LIGHT lint gates with their EXACT scoping (same
# files, severity, and legacy-debt handling as scripts/gates/*), but drives the
# real linters directly so it stays laptop-safe -- no bare `python` (SIGKILL-prone
# and often absent here), and never the unit suite.
#
#   check            mirrors CI gate            scope
#   ruff             repo.format                changed .py vs origin/main (tree has legacy debt)
#   yamllint         repo.yaml                  *.yml/*.yaml, excl. charts/*/templates (not plain YAML)
#   shellcheck       repo.shellcheck            scripts/*, scripts/gates, docker, -S error only
#   helm lint/tmpl   kubernetes.render          charts/redfish-controller
#   kubeconform      kubernetes.schema          rendered chart, -ignore-missing-schemas
#   kube-linter      kubernetes.policy          k8s/ + charts/redfish-controller
#
# The unit suite (unit.all) is NOT run here: 2000+ tests x several agents on one
# laptop exhausts file descriptors/sockets. It runs in the toolbox pipeline. Flow:
#     scripts/lint.sh  ->  git branch neroshige/ci-*  ->  push + dispatch unit.all
#
# A linter whose tool is absent is SKIPPED (reported), not failed; CI still enforces it.
set -u

usage() {
	cat <<'EOF'
scripts/lint.sh -- run the LIGHT local lint gates (ruff, yamllint, shellcheck, helm,
kubeconform, kube-linter) with the same scoping CI uses. Does NOT run unit tests
(those go to the toolbox as unit.all). Absent tools are skipped (reported), not failed.
  scripts/lint.sh          run all available checks; exit non-zero if any fail
  scripts/lint.sh --help   this help
EOF
}

case "${1:-}" in
-h | --help)
	usage
	exit 0
	;;
"") ;;
*)
	echo "unexpected argument: $1" >&2
	usage >&2
	exit 2
	;;
esac

cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:$PATH"
CHART=charts/redfish-controller

fail=0
skipped=""
have() { command -v "$1" >/dev/null 2>&1; }
hdr() { printf '\n=== %s ===\n' "$1"; }
ok() { echo "OK:   $1"; }
bad() {
	echo "FAIL: $1"
	fail=1
}
skip() { skipped="$skipped $1"; }

# 1. ruff -- repo.format: changed .py vs origin/main only (the tree carries legacy debt).
RUFF="${RUFF:-ruff}"
if have "$RUFF"; then
	hdr "ruff (changed .py vs origin/main) [repo.format]"
	git fetch -q origin main 2>/dev/null || true
	if base="$(git merge-base origin/main HEAD 2>/dev/null)"; then
		changed="$(git diff --name-only "$base" HEAD -- '*.py')"
		if [ -z "$changed" ]; then
			ok "ruff (no .py changed vs origin/main)"
		elif printf '%s\n' "$changed" | xargs "$RUFF" check; then
			ok "ruff"
		else
			bad "ruff"
		fi
	else
		echo "  origin/main unresolved -- skipping diff-aware ruff"
		skip "ruff:no-base"
	fi
else
	skip "ruff"
fi

# 2. yamllint -- repo.yaml: concrete YAML only, exclude Helm templates ({{ }} is not plain YAML).
if have yamllint; then
	hdr "yamllint (excl. charts/*/templates) [repo.yaml]"
	yfiles="$(git ls-files '*.yml' '*.yaml' | grep -v '__' | grep -vE 'charts/[^/]+/templates/')"
	if [ -z "$yfiles" ] || printf '%s\n' "$yfiles" | xargs yamllint -d relaxed; then ok "yamllint"; else bad "yamllint"; fi
else
	skip "yamllint"
fi

# 3. shellcheck -- repo.shellcheck: tracked scripts only, at error severity.
if have shellcheck; then
	hdr "shellcheck (scripts, -S error) [repo.shellcheck]"
	sfiles="$(git ls-files 'scripts/*.sh' 'scripts/gates/**/*.sh' 'docker/**/*.sh')"
	if [ -z "$sfiles" ] || printf '%s\n' "$sfiles" | xargs shellcheck -S error; then ok "shellcheck"; else bad "shellcheck"; fi
else
	skip "shellcheck"
fi

# 4. helm -- kubernetes.render: lint + static render of the chart.
if have helm; then
	hdr "helm lint + template [kubernetes.render]"
	if helm lint "$CHART" && helm template "$CHART" >/dev/null; then ok "helm"; else bad "helm"; fi
else
	skip "helm"
fi

# 5. kubeconform -- kubernetes.schema: schema over the rendered chart, skip CRDs/CRs w/o schema.
if have kubeconform && have helm; then
	hdr "kubeconform (rendered chart) [kubernetes.schema]"
	if helm template "$CHART" | kubeconform -ignore-missing-schemas -strict -summary; then ok "kubeconform"; else bad "kubeconform"; fi
else
	skip "kubeconform"
fi

# 6. kube-linter -- kubernetes.policy: security/best-practice over k8s/ and the chart.
if have kube-linter; then
	hdr "kube-linter (k8s/ + chart) [kubernetes.policy]"
	if kube-linter lint k8s/ "$CHART/"; then ok "kube-linter"; else bad "kube-linter"; fi
else
	skip "kube-linter"
fi

hdr "summary"
[ -n "$skipped" ] && echo "skipped (tool absent; install to cover locally):$skipped"
if [ "$fail" -eq 0 ]; then
	echo "LINT OK -- unit tests NOT run here; dispatch unit.all to the toolbox (branch -> push -> ci-scratch)."
else
	echo "LINT FAILED -- fix the FAIL items above before pushing."
fi
exit "$fail"
