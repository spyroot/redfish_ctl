#!/usr/bin/env python3
"""Produce a COMPLETE (unfiltered) Redfish training corpus from a discovery run.

Unlike ``tools/pack_corpus.py`` (which filters to a compact device+telemetry MOCK
corpus by dropping JsonSchemas/Registries/Entries), this keeps EVERYTHING a
``redfish_ctl discovery`` run produced — every JSON resource, all registries,
schemas, actions, settings, and OEM resources — plus the exact ``rest_api_map.npy``
from the same run and a ``corpus_manifest.json``. It is the ``full_training``
artifact. See ``docs/full-corpus-contract.md``.

Redaction: ONLY credential/secret values and account usernames are scrubbed
(reusing ``tools/redact_corpus.SECRET_SUFFIXES`` plus ``username``); every other
value — serials, MACs, IPs, hostnames, schema/registry bodies — is left ORIGINAL.
A full corpus that still carries internal identifiers is INTERNAL/PRIVATE and must
not be committed to a public repo (write it under a gitignored location).

Usage:
    python tools/pack_full_corpus.py ~/.json_responses/<host-id> \\
        full_corpus/<vendor>_<model>_full_corpus.tar.gz \\
        --vendor supermicro --model gb300
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_METHODS = ("GET", "HEAD", "OPTIONS", "POST", "PATCH", "PUT", "DELETE")
REST_API_STATUS_MAP_JSON = "rest_api_map.status.json"
_PACK_METADATA_JSON = {"corpus_manifest.json", REST_API_STATUS_MAP_JSON}
# Credential/secret + account-identity keys to scrub (matched on the last dotted
# segment, case-insensitive). Everything NOT matched is left ORIGINAL — a full
# corpus keeps serials, MACs, IPs, hostnames, schemas, registries as captured.
_REDACT_SUFFIXES = {
    "password", "sha256password", "sha256passwordsalt", "ipmikey", "md5v3key",
    "shav3key", "sha1v3key", "sha256v3key", "communityname", "agentcommunity",
    "snmpv3passphrase", "passphrase", "token", "authtoken", "privatekey",
    "sharedsecret", "presharedkey", "chapsecret", "chapsecretreverse",
    "encryptionkey", "bindpassword", "username", "usernames",
}
# Suffix PATTERNS so a new vendor variant of a known secret class can't slip
# through an exact-match gap (the reason a Dell `SHA1v3Key` once leaked while its
# siblings `MD5v3Key`/`SHA256Password` were scrubbed). A last dotted segment that
# ENDS WITH any of these is treated as the same secret class:
#   *v3key      -> all SNMPv3 localized key material (md5/sha/sha1/sha256 v3key)
#   *community  -> ro/rw/agent/snmp community strings (bare `communityname` in the set)
_REDACT_SUFFIX_PATTERNS = ("v3key", "community")


def _is_secret_key(key: str) -> bool:
    """True if this key's last dotted segment names a credential/secret to scrub."""
    last = key.lower().rsplit(".", 1)[-1]
    if last in _REDACT_SUFFIXES:
        return True
    return any(last.endswith(pat) for pat in _REDACT_SUFFIX_PATTERNS)


def _redact_credentials(obj):
    """Scrub ONLY credential + username values; leave every other value original."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _is_secret_key(k) and isinstance(v, str) and v.strip() and v.lower() != "null":
                out[k] = "REDACTED"
            elif _is_secret_key(k) and isinstance(v, list) and all(isinstance(x, str) for x in v):
                out[k] = ["REDACTED" for _ in v]
            else:
                out[k] = _redact_credentials(v)
        return out
    if isinstance(obj, list):
        return [_redact_credentials(x) for x in obj]
    return obj


def load_api_map(host_dir: Path) -> dict:
    """Load rest_api_map.npy (allow_pickle) and return its dict; raise on problems."""
    import numpy as np
    p = host_dir / "rest_api_map.npy"
    if not p.exists():
        raise FileNotFoundError(f"no rest_api_map.npy in {host_dir} — run `redfish_ctl discovery` first")
    api_map = np.load(p, allow_pickle=True).item()
    for key in ("url_file_mapping", "allowed_methods_mapping"):
        if key not in api_map:
            raise ValueError(f"rest_api_map.npy missing required top-level key '{key}'")
    return api_map


def _resource_json_files(host_dir: Path) -> list[Path]:
    """Return captured resource JSON files, excluding pack metadata sidecars."""
    return sorted(
        p for p in host_dir.glob("*.json")
        if p.name not in _PACK_METADATA_JSON
    )


def _resource_json_paths(json_files: list[Path]) -> list[Path]:
    """Filter a caller-provided JSON file list down to captured resources."""
    return [p for p in json_files if p.name not in _PACK_METADATA_JSON]


def _status_sidecar_payload(api_map: dict) -> dict:
    """Build the JSON status sidecar payload from an API map."""
    return {
        "http_status_mapping": {
            path: int(status)
            for path, status in (api_map.get("http_status_mapping", {}) or {}).items()
        },
        "error_file_mapping": {
            path: str(filename)
            for path, filename in (api_map.get("error_file_mapping", {}) or {}).items()
        },
    }


def _status_sidecar_problems(host_dir: Path, api_map: dict) -> list[str]:
    """Return validation problems for an existing status sidecar, if present."""
    sidecar_path = host_dir / REST_API_STATUS_MAP_JSON
    if not sidecar_path.exists():
        return []
    try:
        sidecar = json.loads(sidecar_path.read_text())
    except (OSError, ValueError) as exc:
        return [f"{REST_API_STATUS_MAP_JSON} is not valid JSON: {exc}"]
    expected = _status_sidecar_payload(api_map)
    problems: list[str] = []
    for key in ("http_status_mapping", "error_file_mapping"):
        if key not in sidecar:
            problems.append(f"{REST_API_STATUS_MAP_JSON} missing required key {key}")
            continue
        if not isinstance(sidecar[key], dict):
            problems.append(f"{REST_API_STATUS_MAP_JSON} {key} must be an object")
            continue
        if sidecar[key] != expected[key]:
            problems.append(f"{REST_API_STATUS_MAP_JSON} {key} does not match rest_api_map.npy")
    return problems


def build_manifest(host_dir: Path, api_map: dict, vendor: str, model: str,
                   json_files: list[Path], redaction_status: str) -> dict:
    """Assemble corpus_manifest.json (counts derived from the map + files)."""
    resource_json = _resource_json_paths(json_files)
    ufm = api_map["url_file_mapping"]
    amm = api_map["allowed_methods_mapping"]
    efm = api_map.get("error_file_mapping", {}) or {}
    hsm = api_map.get("http_status_mapping", {}) or {}
    method_counts = {m: 0 for m in _METHODS}
    for methods in amm.values():
        for m in methods:
            if m in method_counts:
                method_counts[m] += 1
    # bucket captured HTTP statuses (empty on pre-capture corpora)
    status_counts: dict[str, int] = {}
    for status in hsm.values():
        bucket = "unreachable" if status == 0 else f"{status // 100}xx"
        status_counts[bucket] = status_counts.get(bucket, 0) + 1
    redfish_version = ""
    sr = host_dir / "_redfish_v1.json"
    if sr.exists():
        try:
            redfish_version = json.loads(sr.read_text()).get("RedfishVersion", "")
        except (ValueError, OSError):
            pass
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent,
            text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = ""
    return {
        "schema_version": 1,
        "artifact_type": "full_training",
        "vendor": vendor,
        "model": model,
        "host_id": host_dir.name,
        "redfish_version": redfish_version,
        "bmc_firmware_version": "",
        "discovery_timestamp": "",
        "redfish_ctl_commit": commit,
        "json_file_count": len(resource_json),
        "url_file_mapping_count": len(ufm),
        "error_file_mapping_count": len(efm),
        "allowed_methods_mapping_count": len(amm),
        "method_counts": method_counts,
        "http_status_counts": status_counts,
        "redaction_status": redaction_status,
        "artifact_checksum": "",
    }


def artifact_payload_checksum(root: Path) -> str:
    """Return a deterministic checksum for payload files, excluding the manifest."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "corpus_manifest.json"):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def validate(host_dir: Path, api_map: dict, json_files: list[Path]) -> list[str]:
    """Return a list of contract violations (empty = valid). Fail closed on any.

    Backward compatible: ``error_file_mapping`` and ``http_status_mapping`` are
    optional (absent on pre-capture corpora → treated as empty). ``url_file_mapping``
    stays 2xx-only; captured non-2xx responses live in ``error_file_mapping`` and
    every resource JSON on disk must be mapped by exactly one of the two.
    """
    problems: list[str] = []
    ufm = api_map["url_file_mapping"]
    amm = api_map["allowed_methods_mapping"]
    efm = api_map.get("error_file_mapping", {}) or {}
    hsm = api_map.get("http_status_mapping", {}) or {}
    resource_json = _resource_json_paths(json_files)
    names = {p.name for p in resource_json}
    problems.extend(_status_sidecar_problems(host_dir, api_map))
    if "_redfish_v1.json" not in names:
        problems.append("ServiceRoot missing: no _redfish_v1.json (the entry point must be captured)")
    if "/redfish/v1/" not in ufm and "/redfish/v1" not in ufm:
        problems.append("ServiceRoot URL /redfish/v1/ not in url_file_mapping")
    for p in resource_json:
        try:
            json.loads(p.read_text())
        except (ValueError, OSError) as e:
            problems.append(f"unparseable JSON {p.name}: {e}")
    overlap = set(ufm) & set(efm)
    if overlap:
        problems.append(f"url in BOTH url_file_mapping and error_file_mapping: {sorted(overlap)[:5]}")
    if len(resource_json) != len(ufm) + len(efm):
        problems.append(
            f"json_file_count {len(resource_json)} != url_file_mapping {len(ufm)} "
            f"+ error_file_mapping {len(efm)}")
    # methods may cover just the 2xx set (legacy) or the 2xx+error set (new capture)
    if len(amm) not in (len(ufm), len(ufm) + len(efm)):
        problems.append(
            f"allowed_methods_mapping {len(amm)} != url_file_mapping {len(ufm)} "
            f"(or +error_file_mapping {len(efm)})")
    for url, fname in list(ufm.items()) + list(efm.items()):
        if Path(fname).name not in names:
            problems.append(f"mapped file missing from corpus: {url} -> {fname}")
    mapped = {Path(f).name for f in ufm.values()} | {Path(f).name for f in efm.values()}
    for p in resource_json:
        if p.name == "rest_api_map.npy" or p.name == "corpus_manifest.json":
            continue
        if p.name not in mapped:
            problems.append(f"resource JSON not mapped (url_file/error_file): {p.name}")
    for url in amm:
        if url not in ufm and url not in efm:
            problems.append(f"url in allowed_methods_mapping but not in url/error mapping: {url}")
    # http_status keys must resolve to a mapped resource, unless status 0 (an
    # unreachable/transport-failed URL that legitimately has no saved body).
    for url, status in hsm.items():
        if status == 0:
            continue
        if url not in ufm and url not in efm:
            problems.append(f"http_status_mapping url not mapped: {url} ({status})")
    return problems


def pack(host_dir: Path, output: Path, vendor: str, model: str,
         redact: bool = True, dry_run: bool = False) -> int:
    """Validate + (optionally redact) + pack a full corpus. Fail closed."""
    host_dir = host_dir.resolve()
    json_files = _resource_json_files(host_dir)
    if not json_files:
        print(f"no *.json in {host_dir}", file=sys.stderr)
        return 2
    api_map = load_api_map(host_dir)
    problems = validate(host_dir, api_map, json_files)
    if problems:
        print("BLOCKER: full-corpus validation failed (fail closed):", file=sys.stderr)
        for p in problems[:20]:
            print(f"  - {p}", file=sys.stderr)
        return 3
    redaction_status = "original_internal" if not redact else "credentials_username_redacted"
    manifest = build_manifest(host_dir, api_map, vendor, model, json_files, redaction_status)
    print(f"{host_dir.name}: {len(json_files)} json, map {manifest['url_file_mapping_count']} urls, "
          f"methods {manifest['method_counts']}, redact={redact}")
    if dry_run:
        return 0

    staging = Path(tempfile.mkdtemp(prefix="full_corpus_"))
    dest = staging / host_dir.name
    dest.mkdir(parents=True)
    for p in json_files:
        data = json.loads(p.read_text())
        if redact:
            data = _redact_credentials(data)
        (dest / p.name).write_text(json.dumps(data, indent=4))
    # copy the EXACT rest_api_map.npy (same-run; no credentials in URLs/methods)
    (dest / "rest_api_map.npy").write_bytes((host_dir / "rest_api_map.npy").read_bytes())
    sidecar_path = host_dir / REST_API_STATUS_MAP_JSON
    if sidecar_path.exists():
        (dest / REST_API_STATUS_MAP_JSON).write_bytes(sidecar_path.read_bytes())
    elif "http_status_mapping" in api_map or "error_file_mapping" in api_map:
        (dest / REST_API_STATUS_MAP_JSON).write_text(
            json.dumps(_status_sidecar_payload(api_map), indent=2)
        )
    manifest["artifact_checksum"] = artifact_payload_checksum(dest)
    (dest / "corpus_manifest.json").write_text(json.dumps(manifest, indent=2))

    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as tar:
        tar.add(dest, arcname=host_dir.name)
    print(f"wrote {output} (full_training, {len(json_files)} json + map + manifest)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host_dir", type=Path, help="discovery host dir (~/.json_responses/<host-id>)")
    parser.add_argument("output", type=Path, help="output full-corpus .tar.gz (gitignored/private location)")
    parser.add_argument("--vendor", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--no-redact", action="store_true",
                        help="keep the ORIGINAL tree with no redaction (internal-only artifact)")
    parser.add_argument("--dry-run", action="store_true", help="validate + report, do not write")
    args = parser.parse_args(argv)
    return pack(args.host_dir, args.output, args.vendor, args.model,
                redact=not args.no_redact, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
