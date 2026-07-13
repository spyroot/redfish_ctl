#!/usr/bin/env python3
"""Filter a captured Redfish corpus to device + telemetry resources and pack it.

A raw BMC crawl is mostly generic definitions a device mock never needs: DMTF
``JsonSchemas``/``Schemas``, message ``Registries``, and timestamped log/event
*entry* collections. This keeps every device resource (Systems / Chassis /
Managers / Storage / Fabrics and vendor OEM) plus the TelemetryService metric
definitions and reports and the BIOS attribute registry, and drops the rest,
then writes a ``.tar.gz`` (tracked
by Git LFS via the ``*.gz`` rule) so the repo carries one file per corpus instead
of thousands of loose JSON files.

Usage:
    scripts/pack_corpus.py <corpus_leaf_dir> <output.tar.gz> [--arcname NAME] [--dry-run]

``corpus_leaf_dir`` is the directory holding the flattened ``_redfish_v1_*.json``
files (for example, ``build/corpus-staging/dell_xr8620t/json_responses/<capture-id>``).
The tar
stores files under ``<arcname>/`` (default: the leaf dir name), so extracting it
recreates a usable corpus directory.
"""
from __future__ import annotations

import argparse
import re
import sys
import tarfile
from pathlib import Path

# A flattened fixture name is dropped as junk when it matches any of these.
# The BIOS attribute registry is device-specific data (it defines a box's real
# BIOS knobs, unlike the generic DMTF message registries), so it is kept even
# though it lives under /Registries/.
_DROP = re.compile(
    r"^_redfish_v1_JsonSchemas_"                           # DMTF/vendor schema definitions
    r"|^_redfish_v1_Schemas_"                              # CSDL schema documents
    r"|^_redfish_v1_Registries(?!_BiosAttributeRegistry)"  # message registries (keep BiosAttributeRegistry)
    r"|_Entries",                                          # log/event entry collections + members
)


def is_kept(name: str) -> bool:
    """True if a fixture is device/telemetry data worth serving from a mock."""
    return _DROP.search(name) is None


def pack(corpus_dir: Path, output: Path, arcname: str | None, dry_run: bool) -> int:
    files = sorted(corpus_dir.glob("*.json"))
    if not files:
        print(f"no _redfish_v1_*.json files under {corpus_dir}", file=sys.stderr)
        return 2
    kept = [p for p in files if is_kept(p.name)]
    print(
        f"{corpus_dir}: {len(files)} files -> keep {len(kept)}, "
        f"drop {len(files) - len(kept)}"
    )
    if dry_run:
        return 0

    arc = arcname or corpus_dir.name
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as tar:
        for path in kept:
            tar.add(path, arcname=f"{arc}/{path.name}")
    print(f"wrote {output} ({len(kept)} files)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus_dir", type=Path, help="directory of flattened _redfish_v1_*.json")
    parser.add_argument("output", type=Path, help="output .tar.gz path")
    parser.add_argument("--arcname", default=None, help="top dir name inside the tar")
    parser.add_argument("--dry-run", action="store_true", help="count keep/drop only")
    args = parser.parse_args(argv)
    return pack(args.corpus_dir, args.output, args.arcname, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
