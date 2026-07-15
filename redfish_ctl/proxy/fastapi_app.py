"""Optional FastAPI adapter for the read-only proxy core."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from redfish_ctl.api import RedfishApiError

from .core import NodeNotFound, ReadOnlyProxy


def create_app(proxy: ReadOnlyProxy):
    """Create a FastAPI app for read-only proxy routes.

    :param proxy: read-only proxy backing the route handlers.
    :return: configured FastAPI application.
    :raises RuntimeError: when FastAPI is not installed.
    """
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:
        raise RuntimeError(
            "Install FastAPI and an ASGI server to run the read-only proxy."
        ) from exc

    app = FastAPI(title="redfish_ctl read-only proxy")

    def call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Invoke a proxy method and translate errors into HTTP responses.

        :param func: proxy method to call.
        :return: value returned by the proxy method.
        :raises HTTPException: 404 for an unknown node, 502 for a Redfish error.
        """
        try:
            return func(*args, **kwargs)
        except NodeNotFound as exc:
            raise HTTPException(status_code=404, detail=f"unknown node: {exc.args[0]}") from exc
        except RedfishApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/nodes")
    def list_nodes():
        """List registered nodes.

        :return: dict with public metadata for every registered node.
        """
        return proxy.list_nodes()

    @app.get("/nodes/{node_id}")
    def node_status(node_id: str):
        """Return host status and thermal summary for one node.

        :param node_id: id of the node to query.
        :return: dict with node identity, system state, and temperature summary.
        """
        return call(proxy.node_status, node_id)

    @app.get("/nodes/{node_id}/sensors")
    def node_sensors(node_id: str):
        """Return normalized chassis sensor rows for one node.

        :param node_id: id of the node to query.
        :return: dict with node id and a ``sensors`` list of sensor rows.
        """
        return call(proxy.node_sensors, node_id)

    @app.get("/nodes/{node_id}/gpu-metrics")
    def node_gpu_metrics(node_id: str):
        """Return consolidated GPU metric rows for one node.

        :param node_id: id of the node to query.
        :return: dict with node id and ``gpuMetrics`` command data.
        """
        return call(proxy.node_gpu_metrics, node_id)

    @app.get("/nodes/{node_id}/bios")
    def node_bios(node_id: str, attr_filter: str | None = None):
        """Return BIOS attributes for one node.

        :param node_id: id of the node to query.
        :param attr_filter: optional substring filter applied to attribute names.
        :return: dict with node id and ``bios`` attribute command data.
        """
        return call(proxy.node_bios, node_id, attr_filter=attr_filter)

    @app.get("/nodes/{node_id}/metrics")
    def node_metrics(
        node_id: str,
        label_bmc_ip: str | None = None,
        vendor: str | None = None,
    ):
        """Return JSON-safe exporter samples for one node.

        :param node_id: id of the node to query.
        :param label_bmc_ip: BMC IP used as the identity dimension label; falls
            back to the node address host when not set.
        :param vendor: vendor label for identity dimensions; auto-detected when
            not set.
        :return: dict with node id, ``sampleCount``, and JSON-safe ``samples``.
        """
        return call(
            proxy.node_metrics,
            node_id,
            label_bmc_ip=label_bmc_ip,
            vendor=vendor,
        )

    return app
