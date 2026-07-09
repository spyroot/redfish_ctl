#!/usr/bin/env python3
"""Sanitize a crawled Redfish tree so it can be committed as a public test corpus.

`redfish_ctl discovery` writes one JSON file per URI under ``~/.json_responses/<ip>/``.
A full crawl carries identifiers (management IP, MACs, serial/service tags, hostnames),
so it must be scrubbed before it enters the repo. This tool automates the mechanical
part of that: it replaces the source management IP with a documentation address and
redacts a configurable set of identifier fields, writing a clean copy to an output
directory named after the placeholder IP.

Usage::

    python tools/redact_corpus.py ~/.json_responses/10.20.30.40 \
        --out tests/supermicro_new_corpus/json_responses

    # explicit source IP(s) and a custom placeholder:
    python tools/redact_corpus.py /path/to/crawl \
        --out tests/acme_corpus/json_responses \
        --source-ip 10.20.30.40 --source-ip 10.20.30.41 \
        --placeholder-ip 203.0.113.10

It prints only counts, never redacted values. It is a mechanical first pass, NOT a
guarantee — always read the output yourself and run the secret scan in
``docs/fixture-capture.md`` before committing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# RFC 5737 TEST-NET-3 — a documentation address that identifies no real hardware.
DEFAULT_PLACEHOLDER_IP = "203.0.113.10"

# Shape-preserving placeholders for identifier fields, matched case-insensitively by
# exact key name. Values are never emitted to logs.
SENSITIVE_KEYS: dict[str, str] = {
    "serialnumber": "REDACTED-SERIAL",
    "servicetag": "REDACTED-TAG",
    "assettag": "REDACTED-ASSET",
    "sku": "REDACTED-SKU",
    "partnumber": "REDACTED-PART",
    "uuid": "00000000-0000-0000-0000-000000000000",
    "macaddress": "00:00:00:00:00:00",
    "permanentmacaddress": "00:00:00:00:00:00",
    "hostname": "redfish-host",
    "fqdn": "redfish-host.example.com",
    "wwn": "REDACTED-WWN",
    "password": "REDACTED",
    "token": "REDACTED",
}

# Any MAC-shaped string anywhere is scrubbed as a safety net for unexpected fields.
_MAC_RE = re.compile(r"\b([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
_MAC_PLACEHOLDER = "00:00:00:00:00:00"
_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


class Counts:
    """Tallies what was changed, so the CLI can report without printing values."""

    def __init__(self) -> None:
        self.files = 0
        self.key_redactions = 0
        self.ip_replacements = 0
        self.mac_scrubs = 0


def _redact_string(s: str, source_ips: list[str], placeholder_ip: str, counts: Counts) -> str:
    """Replace source IPs and any MAC-shaped substring inside a string value."""
    for ip in source_ips:
        if ip and ip in s:
            s = s.replace(ip, placeholder_ip)
            counts.ip_replacements += 1

    def _mac_sub(m: re.Match) -> str:
        counts.mac_scrubs += 1
        return _MAC_PLACEHOLDER

    return _MAC_RE.sub(_mac_sub, s)


def redact_obj(obj, source_ips: list[str], placeholder_ip: str,
               sensitive_keys: dict[str, str], counts: Counts):
    """Return ``obj`` with sensitive key values redacted and source IPs/MACs scrubbed.

    Recurses through dicts and lists. A dict value is redacted when its key (lowercased)
    is in ``sensitive_keys``; every string leaf is still IP/MAC scrubbed regardless of key.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            placeholder = sensitive_keys.get(key.lower())
            if placeholder is not None and isinstance(value, str):
                out[key] = placeholder
                counts.key_redactions += 1
            elif placeholder is not None and isinstance(value, list) \
                    and all(isinstance(v, str) for v in value):
                out[key] = [placeholder for _ in value]
                counts.key_redactions += len(value)
            else:
                out[key] = redact_obj(value, source_ips, placeholder_ip, sensitive_keys, counts)
        return out
    if isinstance(obj, list):
        return [redact_obj(v, source_ips, placeholder_ip, sensitive_keys, counts) for v in obj]
    if isinstance(obj, str):
        return _redact_string(obj, source_ips, placeholder_ip, counts)
    return obj


def _infer_source_ip(input_dir: Path) -> list[str]:
    """A crawl directory is named after the BMC IP; use it if it looks like one."""
    name = input_dir.name
    return [name] if _IPV4_RE.match(name) else []


def process_tree(input_dir: Path, out_root: Path, source_ips: list[str],
                 placeholder_ip: str) -> Counts:
    """Redact every *.json under ``input_dir`` into ``out_root/<placeholder_ip>/``."""
    counts = Counts()
    dest = out_root / placeholder_ip
    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(input_dir.rglob("*.json")):
        data = json.loads(path.read_text())
        cleaned = redact_obj(data, source_ips, placeholder_ip, SENSITIVE_KEYS, counts)
        rel = path.relative_to(input_dir)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(cleaned, indent=4))
        counts.files += 1
    # .npy rest-api maps embed the source path/IP and cannot be redacted here.
    npy = list(input_dir.rglob("*.npy"))
    if npy:
        print(f"warning: {len(npy)} .npy file(s) skipped — regenerate them from the "
              f"redacted tree or leave them out of the corpus", file=sys.stderr)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input_dir", type=Path, help="crawled tree, e.g. ~/.json_responses/<ip>")
    parser.add_argument("--out", type=Path, required=True,
                        help="output json_responses dir; a <placeholder-ip> subdir is created")
    parser.add_argument("--source-ip", action="append", default=[], dest="source_ips",
                        help="source IP(s) to replace; inferred from the input dir name if omitted")
    parser.add_argument("--placeholder-ip", default=DEFAULT_PLACEHOLDER_IP,
                        help=f"replacement IP (default {DEFAULT_PLACEHOLDER_IP}, RFC 5737)")
    args = parser.parse_args(argv)

    if not args.input_dir.is_dir():
        parser.error(f"input dir not found: {args.input_dir}")
    source_ips = args.source_ips or _infer_source_ip(args.input_dir)
    if not source_ips:
        print("warning: no source IP given or inferable from the dir name; "
              "IP replacement will be skipped (key/MAC redaction still runs)", file=sys.stderr)

    counts = process_tree(args.input_dir, args.out, source_ips, args.placeholder_ip)
    print(f"redacted {counts.files} file(s) -> {args.out / args.placeholder_ip}")
    print(f"  key redactions: {counts.key_redactions}")
    print(f"  ip replacements: {counts.ip_replacements}")
    print(f"  mac scrubs: {counts.mac_scrubs}")
    print("Now read the output and run the secret scan in docs/fixture-capture.md "
          "before committing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
