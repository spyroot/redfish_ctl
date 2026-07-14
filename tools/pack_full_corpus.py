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


def build_manifest(host_dir: Path, api_map: dict, vendor: str, model: str,
                   json_files: list[Path], redaction_status: str) -> dict:
    """Assemble corpus_manifest.json (counts derived from the map + files)."""
    ufm = api_map["url_file_mapping"]
    amm = api_map["allowed_methods_mapping"]
    method_counts = {m: 0 for m in _METHODS}
    for methods in amm.values():
        for m in methods:
            if m in method_counts:
                method_counts[m] += 1
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
        "json_file_count": len(json_files),
        "url_file_mapping_count": len(ufm),
        "allowed_methods_mapping_count": len(amm),
        "method_counts": method_counts,
        "redaction_status": redaction_status,
        "artifact_checksum": "",
    }


def validate(host_dir: Path, api_map: dict, json_files: list[Path]) -> list[str]:
    """Return a list of contract violations (empty = valid). Fail closed on any."""
    problems: list[str] = []
    ufm = api_map["url_file_mapping"]
    amm = api_map["allowed_methods_mapping"]
    names = {p.name for p in json_files}
    if "_redfish_v1.json" not in names:
        problems.append("ServiceRoot missing: no _redfish_v1.json (the entry point must be captured)")
    if "/redfish/v1/" not in ufm and "/redfish/v1" not in ufm:
        problems.append("ServiceRoot URL /redfish/v1/ not in url_file_mapping")
    for p in json_files:
        try:
            json.loads(p.read_text())
        except (ValueError, OSError) as e:
            problems.append(f"unparseable JSON {p.name}: {e}")
    if len(ufm) != len(json_files):
        problems.append(f"json_file_count {len(json_files)} != url_file_mapping {len(ufm)}")
    if len(amm) != len(ufm):
        problems.append(f"allowed_methods_mapping {len(amm)} != url_file_mapping {len(ufm)}")
    for url, fname in ufm.items():
        if Path(fname).name not in names:
            problems.append(f"mapped file missing from corpus: {url} -> {fname}")
    mapped = {Path(f).name for f in ufm.values()}
    for p in json_files:
        if p.name == "rest_api_map.npy" or p.name == "corpus_manifest.json":
            continue
        if p.name not in mapped:
            problems.append(f"resource JSON not in url_file_mapping: {p.name}")
    for url in amm:
        if url not in ufm:
            problems.append(f"url in allowed_methods_mapping but not url_file_mapping: {url}")
    return problems


def pack(host_dir: Path, output: Path, vendor: str, model: str,
         redact: bool = True, dry_run: bool = False) -> int:
    """Validate + (optionally redact) + pack a full corpus. Fail closed."""
    host_dir = host_dir.resolve()
    json_files = sorted(p for p in host_dir.glob("*.json"))
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
    (dest / "corpus_manifest.json").write_text(json.dumps(manifest, indent=2))

    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as tar:
        tar.add(dest, arcname=host_dir.name)
    manifest["artifact_checksum"] = "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest()
    (dest / "corpus_manifest.json").write_text(json.dumps(manifest, indent=2))
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
