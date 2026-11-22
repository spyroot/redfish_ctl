#!/usr/bin/env bash
# unit.all (merge): the full OFFLINE unit suite. Hardware, emulator, and
# dmtf_sim_live tests are explicit profile exclusions, not runtime skips. The
# DMTF lane runs separately in private CI and fails closed when its endpoint is
# absent. The schema-fixture module is also excluded because it performs
# module-level runtime skips when its separately fetched DMTF bundle is absent.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

# The compatibility fixture can select hardware from inherited connection
# variables even when its caller has no live marker. Keep the required gate
# hermetic until that fixture is migrated to the canonical lane contract.
unset \
  REDFISH_IP REDFISH_USERNAME REDFISH_PASSWORD REDFISH_PORT \
  IDRAC_IP IDRAC_USERNAME IDRAC_PASSWORD IDRAC_PORT

pytest_args=(
  -q -ra -W error
  -m "not live and not emulator_live and not dmtf_sim_live"
  --ignore=tests/gates/test_redfish_schema.py
)
if [[ ${REDFISH_COVERAGE_REPORT:-} == true && $# == 0 ]]; then
  set -- --ci-report "${CI_COMMIT_SHA:?CI_COMMIT_SHA is required for coverage evidence}"
fi
unset REDFISH_COVERAGE_REPORT
if (( $# == 0 )); then
  exec pytest "${pytest_args[@]}"
fi

if [[ $# != 2 || $1 != --ci-report || ! $2 =~ ^[0-9a-f]{40}$ ]]; then
  printf 'Usage: %s [--ci-report <commit-sha>]\n' "$0" >&2
  exit 2
fi

checkout_sha=$(git rev-parse HEAD)
if [[ $checkout_sha != "$2" ]]; then
  printf 'Expected checkout %s, found %s\n' "$2" "$checkout_sha" >&2
  exit 1
fi

git lfs ls-files | awk '
  $2 == "-" { print "Unhydrated Git LFS fixture: " $0 > "/dev/stderr"; missing = 1 }
  END { exit missing }
'
reports="$PWD/reports/tests"
mkdir -p "$reports"
rm -f -- "$reports"/{commit.txt,junit.xml,coverage.xml,.coverage}
scratch_root="${REDFISH_CI_SCRATCH_DIR:-$PWD/reports}"
staging="$(mktemp -d "$scratch_root/.test-evidence.XXXXXX")"
trap 'rm -rf -- "$staging"' EXIT
staged_reports="$staging/tests"
mkdir "$staged_reports"
export COVERAGE_FILE="$staging/.coverage"
unset PYTEST_ADDOPTS
pytest_status=0
pytest "${pytest_args[@]}" \
  --junitxml="$staged_reports/junit.xml" \
  --cov --cov-report=term-missing --cov-report="xml:$staged_reports/coverage.xml" ||
  pytest_status=$?

for report in "$staged_reports/junit.xml" "$staged_reports/coverage.xml"; do
  if [[ ! -f $report || -L $report || ! -s $report ]]; then
    printf 'Missing CI test report: %s\n' "$report" >&2
    exit 1
  fi
done
printf '%s\n' "$checkout_sha" > "$staged_reports/commit.txt"
python3 -m tools.ci_evidence "$staging"

mv -- "$staged_reports"/{commit.txt,junit.xml,coverage.xml} "$reports/"
if [[ -f $COVERAGE_FILE && ! -L $COVERAGE_FILE ]]; then
  mv -- "$COVERAGE_FILE" "$reports/.coverage"
fi
exit "$pytest_status"
