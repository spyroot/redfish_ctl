"""Check every cataloged ``hw.*`` telemetry metric for current Splunk MTS flow.

The full-coverage gate is catalog-driven. Metrics use the canary liveness
policy in the shared and vendor telemetry catalogs: ``always_on`` metrics fail
when missing or inactive, while quiet ``condition_gated`` metrics are explicit
not-applicable outcomes. Malformed responses and query failures always fail.

No credential value is printed. A dry run validates the catalog and reports
the selected metric tiers without contacting Splunk Observability.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from tools import splunk_metric_gate as metric_gate

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOGS = (
    REPO_ROOT / "specs" / "telemetry" / "catalog.yaml",
    REPO_ROOT / "specs" / "telemetry" / "supermicro" / "catalog.yaml",
)
LIVENESS_TIERS = frozenset({"always_on", "condition_gated"})

# Exposed as a module seam so offline tests can replace network access.
query_metric = metric_gate.query_metric


@dataclass(frozen=True)
class MetricPolicy:
    """One catalog metric and its scheduled-canary liveness tier."""

    name: str
    liveness: str


def build_parser() -> argparse.ArgumentParser:
    """Build the full-coverage gate parser.

    :return: configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        description="Check every cataloged metric for an active Splunk time series.")
    parser.add_argument(
        "--catalog",
        dest="catalogs",
        type=Path,
        action="append",
        help="metric catalog with canary_liveness policy; repeat for multiple catalogs",
    )
    parser.add_argument("--realm", default=None,
                        help="Splunk realm; defaults to SPLUNK_O11Y_REALM")
    parser.add_argument("--token-env", default="SPLUNK_API_TOKEN",
                        choices=("SPLUNK_ACCESS_TOKEN", "SPLUNK_API_TOKEN"),
                        help="environment variable holding the API token")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="per-request HTTP timeout in seconds")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate catalog tiers without querying Splunk")
    return parser


def load_catalog(paths: Path | list[Path] | tuple[Path, ...]) -> list[MetricPolicy]:
    """Load ordered metric names and liveness tiers from one or more catalogs.

    :param paths: one telemetry catalog path or an ordered path collection.
    :return: ordered metric policies.
    :raises ValueError: when a catalog or liveness policy is incomplete.
    """
    catalog_paths = [paths] if isinstance(paths, Path) else list(paths)
    if not catalog_paths:
        raise ValueError("at least one metric catalog is required")

    metrics: list[MetricPolicy] = []
    seen: set[str] = set()
    for path in catalog_paths:
        try:
            spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"cannot read metric catalog: {path}") from exc
        if not isinstance(spec, dict) or not isinstance(spec.get("metrics"), list):
            raise ValueError(f"metric catalog must contain a metrics list: {path}")

        rows = spec["metrics"]
        catalog_names = [row.get("name") for row in rows if isinstance(row, dict)]
        if (len(catalog_names) != len(rows) or not catalog_names
                or any(not isinstance(name, str) for name in catalog_names)):
            raise ValueError(f"every catalog metric must have a string name: {path}")

        policy = spec.get("canary_liveness")
        if not isinstance(policy, dict):
            raise ValueError(f"metric catalog lacks canary_liveness policy: {path}")
        metric_prefix = policy.get("metric_prefix")
        default = policy.get("default")
        condition_gated = policy.get("condition_gated")
        if not isinstance(metric_prefix, str) or not metric_prefix:
            raise ValueError("canary_liveness must define a non-empty metric_prefix")
        if default not in LIVENESS_TIERS or not isinstance(condition_gated, list):
            raise ValueError(
                "canary_liveness must define a valid default and condition_gated list")
        if any(not isinstance(name, str) for name in condition_gated):
            raise ValueError("condition_gated entries must be metric names")
        names = [name for name in catalog_names if name.startswith(metric_prefix)]
        if not names:
            raise ValueError(f"no catalog metrics match prefix {metric_prefix!r}: {path}")
        duplicates = sorted(set(names) & seen)
        if duplicates:
            raise ValueError(
                f"duplicate metric names across catalogs: {', '.join(duplicates)}")
        unknown = sorted(set(condition_gated) - set(names))
        if unknown:
            raise ValueError(
                f"condition_gated metrics are absent from catalog: {', '.join(unknown)}")

        condition_set = set(condition_gated)
        metrics.extend(
            MetricPolicy(
                name=name,
                liveness="condition_gated" if name in condition_set else default,
            )
            for name in names
        )
        seen.update(names)
    return metrics


def _tier_counts(metrics: list[MetricPolicy]) -> dict[str, int]:
    """Count catalog metrics by liveness tier.

    :param metrics: catalog metric policies.
    :return: tier-to-count mapping.
    """
    return {
        tier: sum(metric.liveness == tier for metric in metrics)
        for tier in sorted(LIVENESS_TIERS)
    }


def run_gate(argv: list[str] | None = None) -> int:
    """Run the catalog-wide scheduled liveness gate.

    :param argv: CLI arguments (None uses sys.argv).
    :return: 0 on success/not-applicable outcomes, 1 on hard liveness failure,
        2 on configuration error.
    """
    args = build_parser().parse_args(argv)
    try:
        metrics = load_catalog(args.catalogs or DEFAULT_CATALOGS)
    except ValueError as exc:
        print(f"splunk-full-coverage: {exc}", file=sys.stderr)
        return 2

    tiers = _tier_counts(metrics)
    if args.dry_run:
        print("splunk-full-coverage: dry-run "
              f"TOTAL={len(metrics)} ALWAYS_ON={tiers['always_on']} "
              f"CONDITION_GATED={tiers['condition_gated']}")
        return 0

    realm, token = metric_gate.resolve_credentials(args.realm, args.token_env)
    if not realm:
        print("splunk-full-coverage: realm is not set", file=sys.stderr)
        return 2
    if not token:
        print(f"splunk-full-coverage: token env {args.token_env} is empty", file=sys.stderr)
        return 2

    counts = {"FLOWING": 0, "INACTIVE": 0, "MISSING": 0, "ERROR": 0}
    hard_failures = 0
    not_applicable = 0
    for metric in metrics:
        try:
            info = query_metric(realm, token, metric.name, args.timeout)
            status = metric_gate.classify_liveness(info)
        except Exception as exc:  # transport/auth/parse failures are hard failures
            status = "ERROR"
            info = {}
            print(f"FAIL ERROR {metric.name}: query error: {type(exc).__name__}: {exc}")
        counts[status] += 1
        if status == "FLOWING":
            print(f"PASS FLOWING {metric.name}: {info['active']}/{info['count']} active")
        elif status == "ERROR":
            if info:
                print(f"FAIL ERROR {metric.name}: active flag missing or malformed")
            hard_failures += 1
        elif metric.liveness == "condition_gated":
            detail = "no time series" if status == "MISSING" else "no active time series"
            print(f"N/A {status} {metric.name}: {detail} (condition-gated quiet state)")
            not_applicable += 1
        else:
            detail = "no time series" if status == "MISSING" else "no active time series"
            print(f"FAIL {status} {metric.name}: {detail} (always-on)")
            hard_failures += 1

    print("splunk-full-coverage: "
          + " ".join(f"{name}={counts[name]}" for name in counts)
          + f" TOTAL={len(metrics)} NOT_APPLICABLE={not_applicable} "
          + f"HARD_FAILURES={hard_failures}")
    return 1 if hard_failures else 0


if __name__ == "__main__":
    sys.exit(run_gate())
