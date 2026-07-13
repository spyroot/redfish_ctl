#!/usr/bin/env python3
"""Compatibility wrapper for the package corpus CLI."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redfish_ctl.corpora import main

if __name__ == "__main__":
    raise SystemExit(main())
