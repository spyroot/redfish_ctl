#!/usr/bin/env bash
# repo.exception-interface (merge, mutates:false): exception TYPES are defined in
# ONE place — the exception interface (redfish_ctl/cmd_exceptions.py and
# redfish_ctl/redfish_exceptions.py). A new exception class defined anywhere else
# fails; a migrated one must leave the baseline (ratchet to zero). This keeps the
# error contract the single top-level exit handler maps to exit codes from being
# fragmented by ad-hoc call-site exception classes.
# Implementation: tools/exception_interface_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/exception_interface_gate.py
