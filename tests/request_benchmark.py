"""Helpers for request-count benchmarks over mocked Redfish services."""

from __future__ import annotations

from typing import Any

from redfish_ctl.idrac_shared import ApiRequestType

RTT_PROFILES = {
    "rack-local": 0.002,
    "office-vpn": 0.080,
    "india-vpn-to-us": 0.300,
    "congested-vpn": 0.800,
}
WRITE_METHODS = {"DELETE", "PATCH", "POST"}


def recorded_requests(
    service: Any,
    *,
    method: str | None = None,
    path: str | None = None,
    start: int = 0,
) -> list[Any]:
    """Return recorded requests, optionally filtered by method and path."""
    method_name = method.upper() if method else None
    expected_path = path.rstrip("/").lower() if path else None
    rows = []
    for request in service.requests[start:]:
        if method_name and request.method != method_name:
            continue
        if expected_path and request.path.rstrip("/").lower() != expected_path:
            continue
        rows.append(request)
    return rows


def projected_walltime(request_count: int, profile: str) -> float:
    """Serial wall-time projection for a request count and latency profile."""
    return request_count * RTT_PROFILES[profile]


def assert_read_budget(
    manager: Any,
    service: Any,
    *,
    api_call: ApiRequestType,
    name: str,
    max_requests: int,
    max_india_vpn_seconds: float,
    **kwargs: Any,
) -> Any:
    """Run one command and assert its request count stays under budget."""
    start = len(service.requests)
    result = manager.sync_invoke(api_call, name, **kwargs)
    requests = service.requests[start:]
    writes = [request for request in requests if request.method in WRITE_METHODS]
    assert not writes, (
        f"{name} benchmark expected a read-only path, "
        f"but saw write methods {[request.method for request in writes]}"
    )

    request_count = len(requests)
    assert request_count <= max_requests, (
        f"{name} used {request_count} BMC round trips; budget is {max_requests}. "
        f"At 300ms RTT that projects to "
        f"{projected_walltime(request_count, 'india-vpn-to-us'):.1f}s."
    )
    assert (
        projected_walltime(request_count, "india-vpn-to-us")
        <= max_india_vpn_seconds
    )
    return result
