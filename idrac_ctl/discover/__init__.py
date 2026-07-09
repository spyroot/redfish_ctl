"""Read-only Redfish network discovery.

This package implements ``redfish-discover``: a non-mutating tool that probes a
list of hosts for a Redfish service root (``/redfish/v1/``) and classifies the
vendor of each reachable service. Nothing here opens a real network connection
or stores credentials — the scan helper takes an async GET callable supplied by
the caller, so the pure logic (vendor classification, result shaping, table
rendering) stays fully offline-testable.

Public surface:

* :func:`classify_vendor` — pure ServiceRoot dict -> vendor string.
* :func:`scan_subnet` — async probe of many hosts via an injected GET callable.
* :func:`make_http_fetcher` — build a real HTTPS GET fetcher for a live scan.
* :func:`redfish_discover_main` — console entry point (rich if available).

Author Mus spyroot@gmail.com
"""
from idrac_ctl.discover.classifier import classify_vendor
from idrac_ctl.discover.cli import make_http_fetcher
from idrac_ctl.discover.scanner import DiscoveredService, scan_subnet

__all__ = [
    "classify_vendor",
    "scan_subnet",
    "make_http_fetcher",
    "DiscoveredService",
]
