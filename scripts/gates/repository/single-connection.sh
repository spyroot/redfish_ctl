#!/usr/bin/env bash
# repo.single-connection (merge, mutates:false): the BMC endpoint/credential set
# (host, username, password, port, is_http) is declared in ONE place — the base
# manager (redfish_ctl/idrac_manager.py) plus redfish_ctl/config.py for the
# environment read. A manager/command class that re-declares an endpoint
# parameter fails (that is a second endpoint), and a redundant pass-through
# __init__ that only forwards *args/**kwargs to super is rejected (run the tool
# with --fix to remove it). This keeps one IP, one username, one password across
# every class; the sim is just another BMC reached through the same one endpoint.
# Implementation: tools/single_connection_gate.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
exec python3 tools/single_connection_gate.py
