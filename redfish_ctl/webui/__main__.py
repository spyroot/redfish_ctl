"""Run the redfish_ctl web explorer:  python -m redfish_ctl.webui

Reads the target BMC from REDFISH_IP/REDFISH_USERNAME/REDFISH_PASSWORD (falling
back to IDRAC_*), the same environment the CLI uses. Every command selected in the
UI is invoked through the tool's own registry — no scripts, no ad-hoc HTTP.
"""

from __future__ import annotations

import argparse

from .server import run_server


def main() -> None:
    """Parse ``--host``/``--port`` and run the web explorer server."""
    parser = argparse.ArgumentParser(prog="redfish_ctl.webui", description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="bind host (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8299, help="bind port (default 8299)")
    args = parser.parse_args()
    run_server(bind_host=args.host, bind_port=args.port)


if __name__ == "__main__":
    main()
