#!/usr/bin/env python3
"""Generate the GB300 telemetry metric catalog markdown.

Audience: both humans and scripted callers. The tool is non-interactive and deterministic:
it reads the committed GB300 corpus tarball, asks the exporter mapper which
metric each observed MetricReport row emits, and either rewrites the catalog or
checks that the checked-in catalog is current.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import tarfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from redfish_ctl.telemetry import exporter  # noqa: E402

DEFAULT_CORPUS = REPO_ROOT / "tests" / "supermicro_gb300_corpus.tar.gz"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "telemetry-metrics.md"
DEFAULT_LEAF = "172.25.230.37"
IDENTITY = exporter.build_identity_dimensions(DEFAULT_LEAF, vendor="supermicro")


@dataclass(frozen=True)
class ReportDefinition:
    """One MetricReportDefinition from the corpus."""

    report: str
    definition_type: str
    metric_properties: tuple[str, ...]


@dataclass(frozen=True)
class ReportData:
    """One MetricReport from the corpus."""

    report: str
    values: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class CatalogRow:
    """One rendered catalog row."""

    metric_name: str
    context: str
    unit: str
    observed_type: str
    expanded_rows: int
    exporter_metric: str


def _load_json_members(tarball: Path, leaf: str) -> dict[str, Mapping[str, object]]:
    """Return JSON payloads under ``leaf`` from ``tarball``, keyed by basename."""
    payloads: dict[str, Mapping[str, object]] = {}
    with tarfile.open(tarball) as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            if not member.name.startswith(f"{leaf}/"):
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            payloads[Path(member.name).name] = json.load(extracted)
    return payloads


def _report_name_from_payload(payload: Mapping[str, object], fallback: str) -> str:
    """Return the report id/name from a MetricReport or MetricReportDefinition."""
    for key in ("Id", "Name"):
        value = payload.get(key)
        if value:
            return str(value)
    return fallback


def _load_definitions(payloads: Mapping[str, Mapping[str, object]]) -> list[ReportDefinition]:
    """Load concrete MetricReportDefinition payloads."""
    prefix = "_redfish_v1_TelemetryService_MetricReportDefinitions_"
    definitions = []
    for name, payload in sorted(payloads.items()):
        if not name.startswith(prefix):
            continue
        report = _report_name_from_payload(payload, name.removeprefix(prefix).removesuffix(".json"))
        metric_properties = tuple(str(item) for item in payload.get("MetricProperties", []) or [])
        definitions.append(
            ReportDefinition(
                report=report,
                definition_type=str(payload.get("MetricReportDefinitionType") or "unknown"),
                metric_properties=metric_properties,
            )
        )
    return definitions


def _load_reports(payloads: Mapping[str, Mapping[str, object]]) -> dict[str, ReportData]:
    """Load concrete MetricReport payloads."""
    prefix = "_redfish_v1_TelemetryService_MetricReports_"
    reports = {}
    for name, payload in sorted(payloads.items()):
        if not name.startswith(prefix):
            continue
        report = _report_name_from_payload(payload, name.removeprefix(prefix).removesuffix(".json"))
        values = []
        for value in payload.get("MetricValues", []) or []:
            if isinstance(value, Mapping):
                values.append(dict(value) | {"Report": report})
        reports[report] = ReportData(report=report, values=tuple(values))
    return reports


def _value_type(value: object) -> str:
    """Classify a Redfish MetricValue string for the catalog."""
    if isinstance(value, bool):
        return "boolean"
    text = str(value).strip()
    if text.lower() in {"true", "false"}:
        return "boolean"
    try:
        float(text)
    except ValueError:
        return "string"
    return "number"


def _value_type_summary(values: Iterable[Mapping[str, object]]) -> str:
    """Return a compact type-count summary."""
    counts = Counter(_value_type(row.get("MetricValue")) for row in values)
    if not counts:
        return "-"
    order = ("boolean", "number", "string")
    return ", ".join(f"{name}:{counts[name]}" for name in order if counts[name])


def _template_regex(template: str) -> re.Pattern[str]:
    """Compile a MetricProperty template with ``{Wildcards}`` into a full regex."""
    pieces = []
    cursor = 0
    for match in re.finditer(r"\{[^}]+\}", template):
        pieces.append(re.escape(template[cursor:match.start()]))
        pieces.append(r"[^/#]+")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


def _matching_values(
    template: str,
    rows: Iterable[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    """Return MetricReport rows whose MetricProperty matches a definition template."""
    pattern = _template_regex(template)
    return [
        row for row in rows
        if pattern.match(str(row.get("MetricProperty") or ""))
    ]


def _property_parts(metric_property: str) -> tuple[list[str], list[str]]:
    """Split a MetricProperty into path and fragment parts."""
    path, _, fragment = metric_property.partition("#")
    path_parts = [part for part in path.strip("/").split("/") if part]
    fragment_parts = [part for part in fragment.strip("/").split("/") if part]
    return path_parts, fragment_parts


def _metric_name(metric_property: str) -> str:
    """Return the display metric name from a MetricProperty template."""
    path_parts, fragment_parts = _property_parts(metric_property)
    parts = fragment_parts or path_parts
    if not parts:
        return "metric"
    if parts[-1].isdigit() and len(parts) > 1:
        return parts[-2]
    return parts[-1]


def _context(metric_property: str) -> str:
    """Return a compact context for a MetricProperty template."""
    path_parts, fragment_parts = _property_parts(metric_property)
    if fragment_parts:
        context_parts = fragment_parts[:-1]
        if context_parts and context_parts[-1].isdigit():
            context_parts = context_parts[:-1]
        if context_parts:
            return "/".join(context_parts)
    if "redfish" in path_parts:
        path_parts = path_parts[path_parts.index("redfish") + 2:]
    return "/".join(path_parts[:-1]) or "-"


def _unit_hint(metric_name: str) -> str:
    """Return a human-readable unit hint when Redfish omits a declared unit."""
    lowered = metric_name.lower()
    if "gbps" in lowered:
        return "Gbps"
    if lowered.endswith("bytes") or lowered.endswith("txbytes") or lowered.endswith("rxbytes"):
        return "By"
    if "mhz" in lowered or "freq" in lowered:
        return "name hint: MHz"
    if "percent" in lowered:
        return "name hint: percent"
    if "temp" in lowered or "temperature" in lowered:
        return "name hint: temperature"
    if "power" in lowered:
        return "name hint: power"
    if "energy" in lowered:
        return "name hint: energy"
    if "voltage" in lowered:
        return "name hint: voltage"
    if "count" in lowered or "errors" in lowered:
        return "name hint: count"
    return "not declared"


def _generic_exporter_name(metric_name: str) -> str:
    """Return the stable generated-metric display name for a definition row."""
    if metric_name.startswith("{") and metric_name.endswith("}"):
        return "`hw.gb300.<resolved_metric_property>`"
    return f"`{exporter._generic_metric_name(metric_name)}`"


def _exporter_metric(
    values: list[Mapping[str, object]],
    metric_name: str,
) -> tuple[str, str]:
    """Return rendered exporter metric names and units for matched rows."""
    if not values:
        return "not observed in fixture", "-"
    samples = exporter.samples_from_metric_report_rows(values, IDENTITY)
    metrics = sorted({sample.metric for sample in samples})
    units = sorted({sample.unit for sample in samples if sample.unit})
    if not metrics:
        rendered_metrics = "not exported by exporter"
    else:
        rendered = []
        if any(metric.startswith("hw.gb300.") for metric in metrics):
            rendered.append(_generic_exporter_name(metric_name))
        rendered.extend(f"`{metric}`" for metric in metrics if not metric.startswith("hw.gb300."))
        rendered_metrics = ", ".join(rendered)
    return rendered_metrics, (", ".join(units) if units else "-")


def _catalog_rows(definition: ReportDefinition, report: ReportData | None) -> list[CatalogRow]:
    """Build rendered catalog rows for one report."""
    values = report.values if report is not None else ()
    rows = []
    for metric_property in definition.metric_properties:
        matched = _matching_values(metric_property, values)
        name = _metric_name(metric_property)
        exporter_metric, exporter_unit = _exporter_metric(matched, name)
        unit = exporter_unit if exporter_unit != "-" else _unit_hint(name)
        rows.append(
            CatalogRow(
                metric_name=name,
                context=_context(metric_property),
                unit=unit,
                observed_type=_value_type_summary(matched),
                expanded_rows=len(matched),
                exporter_metric=exporter_metric,
            )
        )
    return rows


def _inventory_row(definition: ReportDefinition, report: ReportData | None) -> str:
    """Render one report inventory table row."""
    values = report.values if report is not None else ()
    return (
        f"| `{definition.report}` | {definition.definition_type} | "
        f"{len(definition.metric_properties)} | {len(values)} | "
        f"{_value_type_summary(values)} |"
    )


def _md_escape(value: str) -> str:
    """Escape Markdown table syntax in a cell."""
    return value.replace("|", r"\|").replace("\n", " ")


def render_document(
    definitions: list[ReportDefinition],
    reports: Mapping[str, ReportData],
    *,
    corpus: Path,
) -> str:
    """Render the full markdown catalog."""
    lines = [
        "# GB300 Telemetry Metrics",
        "",
        "Author: Mus <spyroot@gmail.com>",
        "",
        "This reference is generated from the Supermicro GB300 fixture files packed in",
        f"`{corpus.relative_to(REPO_ROOT)}`, the captured Redfish JSON corpus used by",
        "the offline tests, so this page does not require a live BMC or private endpoint.",
        "",
        "Regenerate it with `python tools/generate_telemetry_metrics_doc.py`; use",
        "`python tools/generate_telemetry_metrics_doc.py --check` in gates to verify",
        "that the checked-in copy matches the exporter mapper.",
        "",
        "## How To Read This",
        "",
        "`MetricReports`, the Redfish collection represented by the fixture files named",
        "`_redfish_v1_TelemetryService_MetricReports*.json`, carries the observed rows.",
        "`MetricReportDefinitions`, the Redfish collection represented by the fixture",
        "files named `_redfish_v1_TelemetryService_MetricReportDefinitions*.json`,",
        "carries the source metric templates.",
        "",
        "`MetricValue`, the value field in each Redfish `MetricReport` row, is a string",
        "in this corpus. The observed type column below is inferred from those fixture",
        "strings. Units are shown only when the exporter declares one, or as a name",
        "hint when the Redfish report omits a unit. Treat `name hint` entries as",
        "operator guidance, not schema-declared units.",
        "",
        "`Context` is the parent Redfish fragment or path segment that disambiguates",
        "repeated source metric names without copying the full fixture path.",
        "",
        "`redfish_ctl exporter`, defined in `redfish_ctl/telemetry/cmd_exporter.py`,",
        "emits the metrics shown in the `Exporter metric` column. Fabric properties use",
        "curated `hw.fabric.*` names. GPU temperature, processor, throttle, clock, and",
        "memory rows use curated `hw.gpu.*` names. Bounded categorical rows use",
        "`hw.component.*`, `hw.fabric.link_down_reason`, or `hw.power.*` state gauges.",
        "Remaining numeric rows become generated `hw.gb300.*` metric names derived from",
        "the source metric name.",
        "",
        "## Safe Consumption",
        "",
        "Start with direct read-only Redfish GET paths before running a long-lived",
        "exporter process:",
        "",
        "```bash",
        "redfish_ctl metric-definitions",
        "redfish_ctl metric-reports --report HGX_ProcessorPortMetrics_0",
        "```",
        "",
        "Then run a one-shot Prometheus render from `redfish_ctl exporter`, which reads",
        "the BMC and prints text instead of opening a listener:",
        "",
        "```bash",
        "redfish_ctl exporter --vendor supermicro --once --output prometheus",
        "```",
        "",
        "For SignalFx, `SPLUNK_ACCESS_TOKEN`, the ingest token read by the exporter",
        "from the process environment, and `SPLUNK_INGEST_URL`, the full",
        "`/v2/datapoint` URL read by the exporter, are required only when pushing.",
        "Use `--once --output signalfx` first to inspect the datapoint envelope",
        "without posting externally.",
        "",
        "## Checking Live Data In Splunk",
        "",
        "SignalFx is Splunk Observability Cloud, so the exporter's `signalfx` output pushes",
        "these metrics straight into Splunk Observability; no extra bridge is required.",
        "",
        "1. Push to your org. `SPLUNK_INGEST_URL`, the SignalFx `/v2/datapoint` ingest",
        "   URL read by the exporter, and `SPLUNK_ACCESS_TOKEN`, the org access token",
        "   read by the exporter, must come from the environment:",
        "",
        "```bash",
        "export SPLUNK_ACCESS_TOKEN='<org access token>'",
        "export SPLUNK_INGEST_URL='https://ingest.<realm>.signalfx.com/v2/datapoint'",
        "redfish_ctl exporter --vendor supermicro --output signalfx --push-signalfx",
        "```",
        "",
        "2. Find the data in Splunk Observability. Under **Metrics -> Metric Finder**,",
        "   search the metric names this exporter emits: `hw.fabric.*` (NVLink/port",
        "   link state, BER, RX/TX throughput and errors), `hw.gpu.*` (GPU temperature,",
        "   processor, clock, throttle, and memory gauges), `hw.component.*` and",
        "   `hw.power.*` state gauges, `hw.gb300.*` (remaining GB300-specific numeric",
        "   rows), plus `hw.temperature`, `hw.energy_kwh`, and `hw.leak.state` from",
        "   the non-MetricReport samplers. Every datapoint carries these dimensions",
        "   for filtering/grouping: `host.name`, `node`, `server.address`, `bmc.ip`,",
        "   and `vendor`; report-derived datapoints also carry `report` and any",
        "   applicable `gpu`, `port`, `sensor`, `memory`, or state label.",
        "",
        "3. Confirm points are arriving with a chart or SignalFlow query, for example",
        "   fabric receive rate per port on one host:",
        "",
        "```",
        "data('hw.fabric.raw_rx_gbps', filter=filter('host.name', '<bmc-host>')).publish()",
        "```",
        "",
        "Datapoints land within a few seconds of the push; when the Metric Finder shows",
        "the `hw.*` names carrying your `host.name` and `vendor` dimensions, live data",
        "is flowing.",
        "",
        "For **Splunk Enterprise/Cloud (HEC)** rather than Observability: run the Prometheus",
        "listener (`redfish_ctl exporter --output prometheus`, no `--once`) and point a",
        "Splunk OpenTelemetry Collector (prometheus receiver -> `splunk_hec` exporter) at",
        "it, which lands the same metrics in a HEC index.",
        "",
        "For a **native OTLP** pipeline, use `redfish_ctl exporter --output otlp` to push",
        "these same `hw.*` series over OTLP. It honors the standard",
        "`OTEL_EXPORTER_OTLP_*` environment variables and needs the `redfish_ctl[otlp]`",
        "extra. See [Telemetry exporter](telemetry-exporter.md#otlp-opentelemetry).",
        "",
        "> Live verification of the push needs a real `SPLUNK_ACCESS_TOKEN` and your",
        "> realm's `SPLUNK_INGEST_URL`; without them, use `--once --output signalfx`",
        "> to validate the datapoint envelope offline.",
        "",
        "## Report Inventory",
        "",
        "| Report | Definition type | Definition metrics | Observed rows | Observed value types |",
        "|---|---:|---:|---:|---|",
    ]
    for definition in definitions:
        lines.append(_inventory_row(definition, reports.get(definition.report)))
    lines.extend([
        "",
        "## Metric Catalog",
        "",
        "Rows are grouped by Redfish report. `Expanded rows` is the count of concrete",
        "fixture `MetricValue` rows matched by the definition template. `0` means the",
        "definition exists but the current fixture did not include a matching sample.",
        "",
    ])
    for definition in definitions:
        lines.extend([
            f"### `{definition.report}`",
            "",
            "| Metric name | Context | Unit | Observed value type | Expanded rows | Exporter metric |",
            "|---|---|---|---:|---:|---|",
        ])
        for row in _catalog_rows(definition, reports.get(definition.report)):
            lines.append(
                f"| `{_md_escape(row.metric_name)}` | {_md_escape(row.context)} | "
                f"{_md_escape(row.unit)} | {_md_escape(row.observed_type)} | "
                f"{row.expanded_rows} | {_md_escape(row.exporter_metric)} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate docs/telemetry-metrics.md from the committed GB300 corpus. "
            "Audience: both humans and scripted callers."
        )
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS,
                        help="GB300 corpus tarball")
    parser.add_argument("--leaf", default=DEFAULT_LEAF,
                        help="top-level corpus directory inside the tarball")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="markdown output path")
    parser.add_argument("--check", action="store_true",
                        help="fail if the output path is not already current")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    payloads = _load_json_members(args.corpus, args.leaf)
    definitions = _load_definitions(payloads)
    reports = _load_reports(payloads)
    rendered = render_document(definitions, reports, corpus=args.corpus)

    if args.check:
        existing = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if existing != rendered:
            diff = difflib.unified_diff(
                existing.splitlines(),
                rendered.splitlines(),
                fromfile=str(args.output),
                tofile="generated telemetry metrics",
                lineterm="",
            )
            print("\n".join(diff), file=sys.stderr)
            print("BLOCKER: docs/telemetry-metrics.md is stale; regenerate it",
                  file=sys.stderr)
            return 1
        return 0

    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
