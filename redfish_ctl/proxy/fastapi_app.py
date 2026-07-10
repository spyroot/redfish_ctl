"""Optional FastAPI adapter for the read-only proxy core."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from redfish_ctl.api import RedfishApiError

from .core import NodeNotFound, ReadOnlyProxy


def create_app(proxy: ReadOnlyProxy):
    """Create a FastAPI app for read-only proxy routes."""
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:
        raise RuntimeError(
            "Install FastAPI and an ASGI server to run the read-only proxy."
        ) from exc

    app = FastAPI(title="redfish_ctl read-only proxy")

    def call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except NodeNotFound as exc:
            raise HTTPException(status_code=404, detail=f"unknown node: {exc.args[0]}") from exc
        except RedfishApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/nodes")
    def list_nodes():
        return proxy.list_nodes()

    @app.get("/nodes/{node_id}")
    def node_status(node_id: str):
        return call(proxy.node_status, node_id)

    @app.get("/nodes/{node_id}/sensors")
    def node_sensors(node_id: str):
        return call(proxy.node_sensors, node_id)

    @app.get("/nodes/{node_id}/gpu-metrics")
    def node_gpu_metrics(node_id: str):
        return call(proxy.node_gpu_metrics, node_id)

    @app.get("/nodes/{node_id}/bios")
    def node_bios(node_id: str, attr_filter: str | None = None):
        return call(proxy.node_bios, node_id, attr_filter=attr_filter)

    return app
