#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "contracts"
SCHEMA_PATH = ROOT / "schemas" / "protocol-rule-set.schema.json"
GENERATED = ROOT / "generated"
DOCUMENT_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
DOCUMENT_FIELDS = set(DOCUMENT_SCHEMA["properties"])
DOCUMENT_REQUIRED = set(DOCUMENT_SCHEMA["required"])
DOCUMENT_API_VERSION = DOCUMENT_SCHEMA["properties"]["apiVersion"]["const"]
DOCUMENT_KINDS = set(DOCUMENT_SCHEMA["properties"]["kind"]["enum"])


@dataclass(frozen=True)
class LoadedDocument:
    path: Path
    data: dict[str, Any]


class ContractError(RuntimeError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ContractError(f"{path}: expected a YAML mapping")
    return data


def discover_documents(root: Path = CONTRACT_ROOT) -> list[LoadedDocument]:
    documents: list[LoadedDocument] = []
    for path in sorted(root.rglob("*.yaml")):
        documents.append(LoadedDocument(path=path, data=load_yaml(path)))
    return documents


def all_rules(documents: Iterable[LoadedDocument]) -> list[tuple[Path, dict[str, Any]]]:
    found: list[tuple[Path, dict[str, Any]]] = []
    for document in documents:
        rules = document.data.get("spec", {}).get("rules", [])
        if rules is None:
            continue
        if not isinstance(rules, list):
            raise ContractError(f"{document.path}: spec.rules must be a list")
        for rule in rules:
            if not isinstance(rule, dict):
                raise ContractError(f"{document.path}: each rule must be a mapping")
            found.append((document.path, rule))
    return found


def all_statuses(documents: Iterable[LoadedDocument]) -> list[tuple[Path, dict[str, Any]]]:
    found: list[tuple[Path, dict[str, Any]]] = []
    for document in documents:
        statuses = document.data.get("spec", {}).get("statuses", [])
        if statuses is None:
            continue
        if not isinstance(statuses, list):
            raise ContractError(f"{document.path}: spec.statuses must be a list")
        for status in statuses:
            found.append((document.path, status))
    return found


def _status_codes(status: dict[str, Any]) -> set[int]:
    codes: set[int] = set()
    for key in ("emit", "emitPreferred"):
        value = status.get(key)
        if isinstance(value, int):
            codes.add(value)
    accept = status.get("accept", {})
    if isinstance(accept, dict):
        for value in accept.get("values", []) or []:
            if isinstance(value, int):
                codes.add(value)
    return codes


def _iter_status_contexts(value: Any, path: str = "") -> Iterable[tuple[str, dict[str, Any], dict[str, Any]]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key == "status" and isinstance(child, dict) and (
                "accept" in child or "emit" in child or "emitPreferred" in child
            ):
                yield child_path, child, value
            yield from _iter_status_contexts(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_status_contexts(child, f"{path}[{index}]")


def _validate_document_shape(document: LoadedDocument) -> list[str]:
    data = document.data
    errors: list[str] = []
    missing = sorted(DOCUMENT_REQUIRED - data.keys())
    unexpected = sorted(data.keys() - DOCUMENT_FIELDS)
    if missing:
        errors.append(f"{document.path}: missing top-level fields: {missing}")
    if unexpected:
        errors.append(f"{document.path}: unexpected top-level fields: {unexpected}")
    if data.get("apiVersion") != DOCUMENT_API_VERSION:
        errors.append(f"{document.path}: unsupported apiVersion")
    if data.get("kind") not in DOCUMENT_KINDS:
        errors.append(f"{document.path}: unsupported kind {data.get('kind')!r}")
    metadata = data.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
        errors.append(f"{document.path}: metadata.name must be a string")
    elif not metadata["name"]:
        errors.append(f"{document.path}: metadata.name must not be empty")
    if not isinstance(data.get("spec"), dict):
        errors.append(f"{document.path}: spec must be a mapping")
    return errors


def validate_documents(documents: list[LoadedDocument]) -> list[str]:
    errors: list[str] = []
    for document in documents:
        errors.extend(_validate_document_shape(document))

    rules = all_rules(documents)
    rule_ids: dict[str, Path] = {}
    for path, rule in rules:
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            errors.append(f"{path}: rule missing non-empty id")
            continue
        if rule_id in rule_ids:
            errors.append(f"{path}: duplicate rule id {rule_id!r}; first seen in {rule_ids[rule_id]}")
        rule_ids[rule_id] = path

        source = rule.get("source")
        if not isinstance(source, dict):
            errors.append(f"{path}:{rule_id}: missing source mapping")
        else:
            if source.get("document") != "DSP0266":
                errors.append(f"{path}:{rule_id}: source.document must be DSP0266")
            if source.get("version") != "1.24.0":
                errors.append(f"{path}:{rule_id}: source.version must be 1.24.0")
            sections = source.get("sections")
            if not isinstance(sections, list) or not sections:
                errors.append(f"{path}:{rule_id}: source.sections must be a non-empty list")
            if not source.get("statement"):
                errors.append(f"{path}:{rule_id}: source.statement is required")

        if not rule.get("type"):
            errors.append(f"{path}:{rule_id}: rule type is required")
        if "match" not in rule:
            errors.append(f"{path}:{rule_id}: match is required")
        if "expect" not in rule:
            errors.append(f"{path}:{rule_id}: expect is required")

        for status_path, status, parent in _iter_status_contexts(rule):
            codes = _status_codes(status)
            if 201 in codes:
                location = parent.get("headers", {}).get("Location", {}) if isinstance(parent, dict) else {}
                if not isinstance(location, dict) or location.get("presence") != "required":
                    errors.append(f"{path}:{rule_id}:{status_path}: 201 requires Location header")
            if 202 in codes:
                location = parent.get("headers", {}).get("Location", {}) if isinstance(parent, dict) else {}
                if not isinstance(location, dict) or location.get("presence") != "required" or location.get("semantic") != "task_monitor_uri":
                    errors.append(f"{path}:{rule_id}:{status_path}: 202 requires task-monitor Location header")
            if 204 in codes and status.get("emitPreferred") == 204:
                body = parent.get("body") if isinstance(parent, dict) else None
                body_by_status = parent.get("bodyByStatus", {}) if isinstance(parent, dict) else {}
                explicit_forbidden = isinstance(body, dict) and body.get("presence") == "forbidden"
                explicit_by_status = isinstance(body_by_status, dict) and body_by_status.get("204", {}).get("presence") == "forbidden"
                if not (explicit_forbidden or explicit_by_status):
                    errors.append(f"{path}:{rule_id}:{status_path}: preferred 204 must explicitly forbid a response body")
            if status.get("examples") and status.get("examplesExhaustive") is not False:
                source_data = rule.get("source", {})
                if source_data.get("exampleWording") == "such_as":
                    errors.append(f"{path}:{rule_id}:{status_path}: 'such as' examples must be marked non-exhaustive")

        if rule_id == "event.subscription.create.unsupported-parameter":
            body = rule.get("expect", {}).get("body", {})
            if body.get("kind") != "redfish_error" or body.get("presence") != "required":
                errors.append(f"{path}:{rule_id}: exact 400 must require Redfish error body")

    status_codes: dict[int, Path] = {}
    for path, status in all_statuses(documents):
        code = status.get("code")
        if not isinstance(code, int):
            errors.append(f"{path}: status entry missing integer code")
            continue
        if code in status_codes:
            errors.append(f"{path}: duplicate status code {code}; first seen in {status_codes[code]}")
        status_codes[code] = path

    expected = {200, 201, 202, 204, 301, 302, 304, 400, 401, 403, 404, 405, 406, 409, 410, 411, 412, 413, 415, 428, 431, 500, 501, 503, 507}
    missing = sorted(expected - set(status_codes))
    extra = sorted(set(status_codes) - expected)
    if missing:
        errors.append(f"status catalog missing DSP0266 Table 14 codes: {missing}")
    if extra:
        errors.append(f"status catalog has unexpected Table 14 codes: {extra}")

    for document in documents:
        kind = document.data.get("kind")
        spec = document.data.get("spec", {})
        if kind == "CoverageLedger":
            for statement in spec.get("statements", []):
                if statement.get("disposition") == "covered":
                    covered_by = statement.get("coveredBy", [])
                    if not covered_by:
                        errors.append(f"{document.path}:{statement.get('id')}: covered statement has no rule")
                    for rule_id in covered_by:
                        if rule_id not in rule_ids:
                            errors.append(f"{document.path}:{statement.get('id')}: unknown rule {rule_id}")
        if kind == "OemCompatibilityOverlay":
            if "rules" in spec:
                errors.append(f"{document.path}: OEM overlay may not define or replace canonical rules")
            for observation in spec.get("observations", []):
                base = observation.get("baseRule")
                if base not in rule_ids:
                    errors.append(f"{document.path}:{observation.get('id')}: unknown baseRule {base}")
                strict = observation.get("behavior", {}).get("strictDmtfMode", {})
                if strict.get("emit") is True:
                    errors.append(f"{document.path}:{observation.get('id')}: OEM deviation may never be emitted in strict mode")

    return errors


def _json_compact(value: Any) -> str:
    if value in (None, {}, []):
        return ""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _status_summary(expect: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(expect, dict):
        return "", ""
    status = expect.get("status")
    if isinstance(status, dict):
        accept = status.get("accept", {})
        matcher = accept.get("matcher", "") if isinstance(accept, dict) else ""
        values = accept.get("values", []) if isinstance(accept, dict) else []
        if matcher == "derived":
            accepted = f"derived:{accept.get('requiredClass', '')}"
        else:
            accepted = f"{matcher}:{','.join(map(str, values))}" if matcher else ""
        preferred = status.get("emit") or status.get("emitPreferred") or ""
        return accepted, str(preferred)
    alternatives = expect.get("alternatives", [])
    accepted_parts: list[str] = []
    preferred = ""
    for alternative in alternatives:
        alt_status = alternative.get("status", {})
        accept = alt_status.get("accept", {})
        values = accept.get("values", []) if isinstance(accept, dict) else []
        accepted_parts.extend(str(v) for v in values)
        if alt_status.get("emitPreferred") is True and values:
            preferred = str(values[0])
    return "one_of:" + ",".join(accepted_parts) if accepted_parts else "", preferred


def _condition_summary(match: dict[str, Any]) -> dict[str, Any]:
    explicit = match.get("condition") or match.get("preconditions")
    if explicit:
        return explicit
    return {
        key: value
        for key, value in match.items()
        if key not in {"exchange", "attemptedExchange"}
    }


def build_rows(documents: list[LoadedDocument]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, status in all_statuses(documents):
        rows.append({
            "row_kind": "global_status",
            "id": f"http.status.{status['code']}",
            "source_file": str(path.relative_to(ROOT)),
            "source_sections": "8.3/Table14",
            "rule_type": "status_catalog",
            "initiator": "",
            "responder": "redfish_service",
            "method": "*",
            "operation_kind": "",
            "target": "",
            "condition": "",
            "accepted_status": str(status["code"]),
            "preferred_emit": "",
            "headers": _json_compact(status.get("headers")),
            "body": _json_compact(status.get("body")),
            "effects": "",
            "normative_strength": "defined_behavior",
            "summary": status.get("semantic", ""),
        })

    for path, rule in all_rules(documents):
        match = rule.get("match", {})
        exchange = match.get("exchange") or match.get("attemptedExchange") or {}
        target = exchange.get("target", {}) if isinstance(exchange, dict) else {}
        accepted, preferred = _status_summary(rule.get("expect", {}))
        source = rule.get("source", {})
        normative = rule.get("normative", {})
        rows.append({
            "row_kind": "rule",
            "id": rule.get("id", ""),
            "source_file": str(path.relative_to(ROOT)),
            "source_sections": ",".join(str(v) for v in source.get("sections", [])),
            "rule_type": rule.get("type", ""),
            "initiator": _json_compact(exchange.get("initiator")) if isinstance(exchange, dict) else "",
            "responder": _json_compact(exchange.get("responder")) if isinstance(exchange, dict) else "",
            "method": _json_compact(exchange.get("method")) if isinstance(exchange, dict) else "",
            "operation_kind": _json_compact(exchange.get("operationKind")) if isinstance(exchange, dict) else "",
            "target": _json_compact(target),
            "condition": _json_compact(_condition_summary(match)),
            "accepted_status": accepted,
            "preferred_emit": preferred,
            "headers": _json_compact(rule.get("expect", {}).get("headers")),
            "body": _json_compact(rule.get("expect", {}).get("body") or rule.get("expect", {}).get("bodyByStatus")),
            "effects": _json_compact(rule.get("effects")),
            "normative_strength": normative.get("strength", ""),
            "summary": source.get("statement", ""),
        })
    return rows


FIELDNAMES = [
    "row_kind", "id", "source_file", "source_sections", "rule_type",
    "initiator", "responder", "method", "operation_kind", "target",
    "condition", "accepted_status", "preferred_emit", "headers", "body",
    "effects", "normative_strength", "summary",
]


def render_generated(documents: list[LoadedDocument]) -> dict[Path, str]:
    rows = build_rows(documents)
    json_text = json.dumps({"schemaVersion": 1, "rows": rows}, indent=2, sort_keys=False) + "\n"

    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    csv_text = csv_buffer.getvalue()

    md_lines = [
        "# Effective Redfish HTTP semantic matrix",
        "",
        "Generated from the canonical YAML contracts. Do not edit manually.",
        "",
        "| ID | Type | Method | Accepted status | Preferred emit | Source | Summary |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        md_lines.append(
            f"| `{row['id']}` | {row['rule_type']} | `{row['method']}` | "
            f"`{row['accepted_status']}` | `{row['preferred_emit']}` | "
            f"{row['source_sections']} | {row['summary']} |"
        )
    md_text = "\n".join(md_lines) + "\n"

    event_rows = [r for r in rows if r["id"].startswith("event.")]
    event_buffer = io.StringIO(newline="")
    event_writer = csv.DictWriter(event_buffer, fieldnames=FIELDNAMES, lineterminator="\n")
    event_writer.writeheader()
    event_writer.writerows(event_rows)

    return {
        GENERATED / "dmtf-effective-matrix.json": json_text,
        GENERATED / "dmtf-effective-matrix.csv": csv_text,
        GENERATED / "dmtf-effective-matrix.md": md_text,
        GENERATED / "eventing-matrix.csv": event_buffer.getvalue(),
    }


def write_generated(outputs: dict[Path, str], check: bool) -> list[str]:
    errors: list[str] = []
    for path, content in outputs.items():
        if check:
            if not path.exists():
                errors.append(f"missing generated file: {path.relative_to(ROOT)}")
            elif path.read_text(encoding="utf-8") != content:
                errors.append(f"generated file is stale: {path.relative_to(ROOT)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return errors


def _match_scalar(expected: Any, actual: Any) -> bool:
    if expected == "*":
        return True
    if isinstance(expected, list):
        return actual in expected
    return expected == actual


def _match_state(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        if "notIn" in expected:
            return actual not in expected["notIn"]
        if "in" in expected:
            return actual in expected["in"]
        if not isinstance(actual, dict):
            return False
        return all(_match_state(value, actual.get(key)) for key, value in expected.items())
    if isinstance(expected, list):
        return actual in expected
    return expected == actual


def _operation_matches(rule: dict[str, Any], observation: dict[str, Any]) -> bool:
    match = rule.get("match", {})
    exchange = match.get("exchange") or match.get("attemptedExchange")
    operation = observation.get("operation", {})
    if not isinstance(exchange, dict):
        return False
    for key in ("initiator", "responder", "method", "operationKind"):
        if key in exchange and not _match_scalar(exchange[key], operation.get(key)):
            return False
    expected_target = exchange.get("target", {})
    actual_target = operation.get("target", {})
    for key, value in expected_target.items():
        if not _match_scalar(value, actual_target.get(key)):
            return False
    actual_condition = {
        **observation.get("condition", {}),
        **observation.get("preconditions", {}),
    }
    for state_key in ("condition", "preconditions"):
        expected_state = match.get(state_key, {})
        if not _match_state(expected_state, actual_condition):
            return False
    return True


def _status_node_accepts(node: dict[str, Any], status_code: int) -> bool:
    accept = node.get("accept", {})
    matcher = accept.get("matcher")
    values = accept.get("values", []) or []
    if matcher in {"exact", "one_of"}:
        return status_code in values
    if matcher == "class":
        return f"{status_code // 100}xx" in values
    if matcher == "derived":
        return accept.get("requiredClass") == f"{status_code // 100}xx"
    return False


def _response_shape_matches(
    expectation: dict[str, Any],
    response: dict[str, Any],
    status_code: int,
) -> bool:
    actual_headers = {
        str(key).lower(): value
        for key, value in response.get("headers", {}).items()
    }
    for name, contract in expectation.get("headers", {}).items():
        if isinstance(contract, dict) and contract.get("presence") == "required":
            if name.lower() not in actual_headers:
                return False

    body_contract = expectation.get("body")
    body_by_status = expectation.get("bodyByStatus", {})
    if isinstance(body_by_status, dict):
        body_contract = body_by_status.get(str(status_code), body_contract)
    if isinstance(body_contract, dict):
        body_present = "body" in response and response["body"] is not None
        if body_contract.get("presence") == "required" and not body_present:
            return False
        if body_contract.get("presence") == "forbidden" and body_present:
            return False
    return True


def _response_accepts(rule: dict[str, Any], response: dict[str, Any]) -> bool:
    status_code = response.get("status")
    if not isinstance(status_code, int):
        return False
    expect = rule.get("expect", {})
    candidates = [expect, *(expect.get("alternatives", []) or [])]
    for candidate in candidates:
        status = candidate.get("status")
        if isinstance(status, dict) and _status_node_accepts(status, status_code):
            return _response_shape_matches(candidate, response, status_code)
    return False


def classify(observation_path: Path, overlay_path: Path | None) -> dict[str, Any]:
    documents = discover_documents()
    observation = load_yaml(observation_path)
    status = observation.get("response", {}).get("status")
    if not isinstance(status, int):
        raise ContractError("observation response.status must be an integer")
    candidates: list[dict[str, Any]] = []
    for _, rule in all_rules(documents):
        if _operation_matches(rule, observation):
            candidates.append(rule)
    response = observation.get("response", {})
    accepted = [rule for rule in candidates if _response_accepts(rule, response)]
    result: dict[str, Any] = {
        "observation": str(observation_path),
        "status": status,
        "matchedRules": [r["id"] for r in candidates],
        "acceptedByDmtfRules": [r["id"] for r in accepted],
        "strictDmtf": "accepted" if accepted else "rejected",
        "oemCompatibility": "not_evaluated",
    }
    if overlay_path:
        overlay = load_yaml(overlay_path)
        compatibility_hits: list[str] = []
        for item in overlay.get("spec", {}).get("observations", []):
            observed = item.get("observed", {})
            if observed.get("status") == status and item.get("baseRule") in result["matchedRules"]:
                if item.get("behavior", {}).get("oemCompatibilityMode", {}).get("accept") is True:
                    compatibility_hits.append(item.get("id", ""))
        result["oemCompatibility"] = "accepted_with_warning" if compatibility_hits else "rejected"
        result["oemObservations"] = compatibility_hits
    return result


def cmd_validate(_: argparse.Namespace) -> int:
    documents = discover_documents()
    errors = validate_documents(documents)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Validated {len(documents)} contract documents and {len(all_rules(documents))} rules.")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    documents = discover_documents()
    errors = validate_documents(documents)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    outputs = render_generated(documents)
    errors = write_generated(outputs, check=args.check)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    verb = "Checked" if args.check else "Generated"
    print(f"{verb} {len(outputs)} matrix artifacts.")
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    result = classify(Path(args.observation), Path(args.overlay) if args.overlay else None)
    print(json.dumps(result, indent=2))
    return 0 if result["strictDmtf"] == "accepted" or result["oemCompatibility"] == "accepted_with_warning" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and generate Redfish semantic contracts")
    sub = parser.add_subparsers(dest="command", required=True)
    validate_parser = sub.add_parser("validate", help="validate all contracts")
    validate_parser.set_defaults(func=cmd_validate)
    generate_parser = sub.add_parser("generate", help="generate flattened matrices")
    generate_parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    generate_parser.set_defaults(func=cmd_generate)
    classify_parser = sub.add_parser("classify", help="classify a response observation")
    classify_parser.add_argument("observation")
    classify_parser.add_argument("--overlay")
    classify_parser.set_defaults(func=cmd_classify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ContractError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
