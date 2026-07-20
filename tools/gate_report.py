#!/usr/bin/env python3
"""Generate a SANITIZED gate report from the gate registry (and optional results).

Reads ``gates/manifest.yaml`` and, when present, a JSON results map ``{gate_id: "pass"|"fail"|"skip"}``
produced by a gate run, then writes a Markdown report. Every emitted line is scrubbed for
secret-shaped tokens so the artifact is safe to publish (the ``evidence.sanitized`` gate re-checks
the written file). No timestamps or host identifiers are embedded, keeping the output deterministic.

    python3 tools/gate_report.py                         # registry-only report to stdout
    python3 tools/gate_report.py --results results.json --out reports/gates/gate-report.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "gates" / "manifest.yaml"

# Secret-shaped patterns redacted from every output line (defence-in-depth; the report never
# intentionally contains a value, this catches an accidental one before it reaches an artifact).
_SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+"),
]


def _redact(line: str) -> str:
    """Replace any secret-shaped substring in a line with ``[REDACTED]``.

    :param line: a single output line.
    :return: the line with secret-shaped tokens masked.
    """
    for pat in _SECRET_PATTERNS:
        line = pat.sub("[REDACTED]", line)
    return line


def _load_manifest() -> dict:
    """Load the gate registry via PyYAML, falling back to a tiny line parser when PyYAML is absent.

    :return: the parsed registry mapping.
    :raises SystemExit: when the manifest cannot be read.
    """
    text = MANIFEST.read_text()
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        return _mini_parse(text)


def _mini_parse(text: str) -> dict:
    """Parse the registry's ``gates:`` list without PyYAML (id/profile/required/mutates only).

    :param text: raw manifest text.
    :return: a mapping with ``mandatory_ids`` and a ``gates`` list of dicts.
    """
    gates: list[dict] = []
    cur: dict = {}
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("- id:"):
            if cur:
                gates.append(cur)
            cur = {"id": s.split(":", 1)[1].strip()}
        elif ":" in s and cur and not s.startswith("#"):
            k, v = s.split(":", 1)
            cur[k.strip()] = v.strip()
    if cur:
        gates.append(cur)
    return {"gates": gates}


def build_report(results: dict | None) -> str:
    """Build the Markdown report body from the registry and optional results.

    :param results: optional ``{gate_id: status}`` map; ``None`` renders a registry-only report.
    :return: the sanitized Markdown report as a string.
    """
    reg = _load_manifest()
    gates = reg.get("gates", [])
    lines = ["# Gate report (sanitized)", ""]
    lines.append("Generated from `gates/manifest.yaml`. Secret-shaped tokens are redacted; this file is")
    lines.append("safe to attach as a CI artifact. Status is `-` when the report is registry-only.")
    lines.append("")
    lines.append("| id | profile | mutates | required | status |")
    lines.append("| -- | ------- | ------- | -------- | ------ |")
    for g in gates:
        gid = str(g.get("id", "?"))
        status = (results or {}).get(gid, "-")
        lines.append(
            f"| `{gid}` | {g.get('profile', '?')} | {g.get('mutates', '?')} | "
            f"{g.get('required', '?')} | {status} |"
        )
    lines.append("")
    if results:
        failed = sorted(k for k, v in results.items() if v == "fail")
        skipped = sorted(k for k, v in results.items() if v == "skip")
        lines.append(f"**Summary:** {len(results)} gates reported, "
                     f"{len(failed)} failed, {len(skipped)} skipped.")
        if failed:
            lines.append(f"**Failed:** {', '.join(failed)}")
        if skipped:
            lines.append(f"**Skipped (treated as FAIL):** {', '.join(skipped)}")
    else:
        lines.append(f"**Summary:** {len(gates)} gates registered (registry-only, no run attached).")
    return "\n".join(_redact(x) for x in lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry: render the report to stdout or a file.

    :param argv: optional argument vector (defaults to ``sys.argv``).
    :return: process exit code (0 on success).
    """
    ap = argparse.ArgumentParser(description="Generate a sanitized gate report.")
    ap.add_argument("--results", help="JSON file mapping gate_id -> pass|fail|skip")
    ap.add_argument("--out", help="write the report here (default: stdout)")
    args = ap.parse_args(argv)

    results = None
    if args.results:
        results = json.loads(Path(args.results).read_text())
    report = build_report(results)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
