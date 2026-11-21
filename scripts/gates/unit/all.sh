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
mkdir -p reports/tests
printf '%s\n' "$checkout_sha" > reports/tests/commit.txt
export COVERAGE_FILE="$PWD/reports/tests/.coverage"
unset PYTEST_ADDOPTS
pytest "${pytest_args[@]}" \
  --junitxml=reports/tests/junit.xml \
  --cov --cov-report=term-missing --cov-report=xml:reports/tests/coverage.xml

for report in reports/tests/junit.xml reports/tests/coverage.xml; do
  if [[ ! -s $report ]]; then
    printf 'Missing CI test report: %s\n' "$report" >&2
    exit 1
  fi
done
