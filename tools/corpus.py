#!/usr/bin/env python3
"""Manage the committed Redfish corpus library.

The corpora are one Git-LFS ``.tar.gz`` per captured box under ``tests/`` (built
by ``tools/pack_corpus.py``), indexed by ``tests/corpus/manifest.json``. This CLI
is the single documented entry point for pulling every corpus and materializing
the JSON — see ``docs/external/corpus-library.md``.

Subcommands
-----------
list          Print the manifest (vendor, model, Redfish version, JSON count).
pull          ``git lfs pull`` the corpus tarballs (all, or --vendor/--model).
extract-all   Extract every corpus into ``<dest>/<vendor>_<model>/`` (full JSON
              tree for consumers such as the igc pipeline that need all files).
verify        Assert every tarball exists, is LFS-tracked, and its ``.json``
              count matches the manifest (bare LFS pointers are skipped, not
              failed, so the check works before ``git lfs pull``).
self-check    Offline: diff each corpus against ITSELF through the generic
              discovery engine (``tools/corpus_diff.py``) — proves the walk is
              vendor-generic and the corpus is self-consistent. No network.
live-diff     Read-only (GET-only) diff of one corpus against a live BMC via
              the ``redfish_ctl`` client: stable identity/config fields only,
              volatile state ignored. ``--dry-run`` prints the fetch plan
              without touching the network.

    python tools/corpus.py self-check
    python tools/corpus.py live-diff --vendor supermicro --model gb300 --ip <bmc-ip>
    python tools/corpus.py live-diff --vendor dell --model xr8620t --ip <bmc-ip> --dry-run

Audience: agent | human. The two diff subcommands emit a JSON report on stdout
(diagnostics on stderr) and exit 0 on match/gaps-only, 1 on drift, 2 on a
usage/environment error. BMC credentials come from the gitignored inventory
(``.internal/inventory/env-inventory.yaml``) or the ``REDFISH_USERNAME`` /
``REDFISH_PASSWORD`` environment variables — never from argv, never printed.

The module is also importable: :func:`load_manifest` returns the parsed rows and
:func:`resolve` maps ``(vendor, model)`` to a row, so callers can locate a corpus
by vendor/model instead of the raw capture-IP ``arcname``.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

try:  # imported as tools.corpus (tests) or run as a script from tools/
    from tools import corpus_diff
except ImportError:  # pragma: no cover - script-run path (sys.path[0] = tools/)
    import corpus_diff

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "tests" / "corpus" / "manifest.json"
# Repo-anchored (not cwd-relative) so the tool works from any working directory.
DEFAULT_INVENTORY = REPO_ROOT / ".internal" / "inventory" / "env-inventory.yaml"


def load_manifest(path: Path = MANIFEST_PATH) -> list[dict]:
    """Return the corpus rows from the manifest JSON."""
    data = json.loads(Path(path).read_text())
    return list(data.get("corpora", []))


def resolve(vendor: str, model: str, path: Path = MANIFEST_PATH) -> Optional[dict]:
    """Return the manifest row for ``(vendor, model)`` (case-insensitive), or None."""
    vendor, model = vendor.lower(), model.lower()
    for row in load_manifest(path):
        if row["vendor"].lower() == vendor and row["model"].lower() == model:
            return row
    return None


def _select(rows: list[dict], vendor: Optional[str], model: Optional[str]) -> list[dict]:
    """Filter rows by optional vendor and/or model (case-insensitive)."""
    out = rows
    if vendor:
        out = [r for r in out if r["vendor"].lower() == vendor.lower()]
    if model:
        out = [r for r in out if r["model"].lower() == model.lower()]
    return out


def _tarball_path(row: dict) -> Path:
    """Absolute path to a row's tarball."""
    return REPO_ROOT / row["tarball"]


def _is_lfs_pointer(path: Path) -> bool:
    """True if ``path`` is still a bare Git-LFS pointer (not yet ``git lfs pull``ed)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(120)
    except OSError:
        return False
    return head.startswith(b"version https://git-lfs.github.com/spec")


def _count_json(path: Path) -> int:
    """Count ``.json`` members in a corpus tarball."""
    with tarfile.open(path) as tar:
        return sum(1 for name in tar.getnames() if name.endswith(".json"))


def cmd_list(args: argparse.Namespace) -> int:
    """Print the manifest as a table."""
    rows = _select(load_manifest(), args.vendor, args.model)
    if not rows:
        print("no corpora match the filter", file=sys.stderr)
        return 1
    total = 0
    print(f"{'VENDOR':<11} {'MODEL':<12} {'REDFISH':<8} {'JSON':>6}  TARBALL")
    for row in rows:
        total += int(row["json_count"])
        print(f"{row['vendor']:<11} {row['model']:<12} "
              f"{row['redfish_version']:<8} {row['json_count']:>6}  {row['tarball']}")
    print(f"{'':<11} {'':<12} {'total':<8} {total:>6}")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """``git lfs pull`` the selected corpus tarballs."""
    rows = _select(load_manifest(), args.vendor, args.model)
    if not rows:
        print("no corpora match the filter", file=sys.stderr)
        return 1
    includes = ",".join(row["tarball"] for row in rows)
    cmd = ["git", "lfs", "pull", f"--include={includes}"]
    print("running:", " ".join(cmd))
    return subprocess.call(cmd, cwd=REPO_ROOT)


def cmd_extract_all(args: argparse.Namespace) -> int:
    """Extract selected corpora into ``<dest>/<vendor>_<model>/``."""
    rows = _select(load_manifest(), args.vendor, args.model)
    if not rows:
        print("no corpora match the filter", file=sys.stderr)
        return 1
    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    pending = [r for r in rows if _is_lfs_pointer(_tarball_path(r))]
    if pending:
        names = ", ".join(f"{r['vendor']}/{r['model']}" for r in pending)
        print(f"error: not pulled (bare LFS pointer): {names}\n"
              f"run `python tools/corpus.py pull` first", file=sys.stderr)
        return 1
    for row in rows:
        out = dest / f"{row['vendor']}_{row['model']}"
        out.mkdir(parents=True, exist_ok=True)
        with tarfile.open(_tarball_path(row)) as tar:
            try:
                tar.extractall(out, filter="data")  # py3.12+ path-safe filter
            except TypeError:  # pragma: no cover - py<3.12 lacks the kwarg
                tar.extractall(out)
        print(f"extracted {row['json_count']:>5} json  {row['vendor']}/{row['model']} -> {out}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Check tarballs exist and their JSON counts match the manifest."""
    rows = _select(load_manifest(), args.vendor, args.model)
    ok = True
    for row in rows:
        path = _tarball_path(row)
        if not path.exists():
            print(f"MISSING  {row['tarball']}")
            ok = False
            continue
        if _is_lfs_pointer(path):
            print(f"pointer  {row['tarball']} (not pulled; skipped count check)")
            continue
        actual = _count_json(path)
        if actual != int(row["json_count"]):
            print(f"MISMATCH {row['tarball']}: manifest={row['json_count']} actual={actual}")
            ok = False
        else:
            print(f"ok       {row['tarball']} ({actual} json)")
    return 0 if ok else 1


@contextlib.contextmanager
def _extracted(row: dict):
    """Extract one corpus tarball to a temp dir, yield its leaf, always clean up.

    ``try/finally`` (not ``atexit``) removes the tree, so even an exception
    mid-comparison cannot leave a ~1600-file extraction behind, and disk use is
    bounded to one corpus at a time.

    :param row: manifest row (``tarball`` + ``arcname``).
    :return: context manager yielding the flattened-fixture directory.
    """
    tmp = Path(tempfile.mkdtemp(prefix="corpus_diff_"))
    try:
        with tarfile.open(_tarball_path(row)) as tar:
            try:
                tar.extractall(tmp, filter="data")  # py3.12+ path-safe filter
            except TypeError:  # pragma: no cover - py<3.12 lacks the kwarg
                tar.extractall(tmp)
        yield tmp / row["arcname"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _corpus_ready(row: dict) -> Optional[str]:
    """Return why a corpus tarball is unusable, or None when it is ready.

    A missing file and a bare LFS pointer are both environment states (exit 2
    territory), never allowed to surface as a traceback or a drift exit.

    :param row: manifest row to check.
    :return: a human-readable reason with the next step, or None when usable.
    """
    path = _tarball_path(row)
    if not path.exists():
        return (f"{row['tarball']} is missing — check tests/corpus/manifest.json "
                "or run `git lfs pull`")
    if _is_lfs_pointer(path):
        return (f"{row['tarball']} is a bare LFS pointer — run "
                f"`python tools/corpus.py pull --vendor {row['vendor']} "
                f"--model {row['model']}`")
    return None


def _bmc_credentials(ip: str, inventory: Path) -> tuple[str, str]:
    """Resolve BMC credentials for ``ip`` — inventory first, then environment.

    Values are used in memory only and never printed. Passwords are NEVER
    accepted on argv (argv leaks into process listings and shell history).

    :param ip: BMC IP whose credentials to resolve.
    :param inventory: gitignored inventory YAML (``nodes[].bmc{ip,user,password}``).
    :return: ``(username, password)``.
    :raises SystemExit: exit code 2 (message on stderr) when nothing resolves.
        A bare string ``SystemExit`` would exit 1 and collide with the drift
        code, so the message is printed and the code raised explicitly.
    """
    if inventory.exists():
        import yaml
        inv = yaml.safe_load(inventory.read_text())
        for node in (inv or {}).get("nodes", []):
            bmc = node.get("bmc") or {}
            if str(bmc.get("ip")) == ip:
                user, password = bmc.get("user"), bmc.get("password")
                if user and password:  # a half-filled node must not become "None"
                    return str(user), str(password)
                break  # fall through to the env pair / the loud error
    user = os.environ.get("REDFISH_USERNAME") or os.environ.get("IDRAC_USERNAME")
    password = os.environ.get("REDFISH_PASSWORD") or os.environ.get("IDRAC_PASSWORD")
    if user and password:
        return user, password
    print(f"error: no credentials for BMC {ip}: not in {inventory} and "
          "REDFISH_USERNAME/REDFISH_PASSWORD are unset\n"
          "next step: add the node to the inventory or export the env pair",
          file=sys.stderr)
    raise SystemExit(2)


def _live_fetcher(ip: str, username: str, password: str) -> "corpus_diff.Fetcher":
    """Build the live-side fetcher, routed through the ``redfish_ctl`` client.

    All Redfish I/O goes through the project client (raw HTTP is forbidden by
    the team contract); every call is a plain GET. A transport or protocol
    failure is reported on stderr and surfaces as a ``live_gap`` row — one
    unreachable resource must not abort the whole diff.

    :param ip: BMC host or IP.
    :param username: BMC account username.
    :param password: BMC account password (in memory only).
    :return: fetcher mapping a resource path to its parsed dict or None.
    """
    from redfish_ctl.idrac_manager import IDracManager
    manager = IDracManager(host=ip, username=username,
                           password=password, insecure=True)

    def fetch(path: str) -> Optional[dict]:
        """GET one live resource; None on any failure (reported to stderr).

        A rejected credential aborts the whole run (exit 2) instead of turning
        every resource into a gap: retrying a bad login per-resource reports a
        useless all-gap result and risks locking the BMC account.

        :param path: Redfish resource path.
        :return: parsed resource dict, or None when the GET fails.
        :raises SystemExit: exit code 2 on an authentication/authorization
            failure.
        """
        try:
            result = manager.base_query(path)
        except Exception as exc:  # any one failed GET is a gap, not an abort
            name = exc.__class__.__name__
            if any(word in name for word in
                   ("Authentication", "Unauthorized", "Forbidden")):
                print(f"error: BMC rejected the credentials on {path} ({name})\n"
                      "next step: fix the inventory entry or the "
                      "REDFISH_USERNAME/REDFISH_PASSWORD pair", file=sys.stderr)
                raise SystemExit(2)
            # Class name ONLY — an exception's message can embed transport
            # details and must never risk echoing credential material.
            print(f"live {path}: {name}", file=sys.stderr)
            return None
        data = getattr(result, "data", None)
        return data if isinstance(data, dict) else None

    return fetch


def _summary_exit(summary: dict) -> int:
    """Map one comparison summary to the exit-code contract.

    :param summary: :func:`corpus_diff.compare` summary.
    :return: 2 when NOTHING was checked (an empty/wrong corpus must never
        pass) or when a collection was paginated (page one is not the full
        member set — the comparison is incomplete), 1 on drift, else 0.
    """
    if summary["checked"] == 0 or summary.get("paginated"):
        return 2
    return 0 if summary["ok"] else 1


def _stderr_line(mode: str, label: str, summary: dict) -> None:
    """Print the one-line human summary for a comparison to stderr.

    :param mode: ``self-check`` or ``live-diff``.
    :param label: ``vendor/model`` label of the compared corpus.
    :param summary: :func:`corpus_diff.compare` summary.
    """
    print(f"{mode} {label}: {summary['matched']}/{summary['checked']} "
          f"stable fields match, drift={summary['drift']} gaps={summary['gaps']}",
          file=sys.stderr)


def cmd_self_check(args: argparse.Namespace) -> int:
    """Diff each selected corpus against itself (offline genericity check)."""
    rows = _select(load_manifest(), args.vendor, args.model)
    if not rows:
        print("no corpora match the filter\n"
              "next step: `python tools/corpus.py list`", file=sys.stderr)
        return 2
    reports, skipped, worst = [], [], 0
    for row in rows:
        label = f"{row['vendor']}/{row['model']}"
        reason = _corpus_ready(row)
        if reason:
            skipped.append({"corpus": label, "reason": reason})
            print(f"self-check {label}: skipped — {reason}", file=sys.stderr)
            continue
        with _extracted(row) as corpus_dir:
            fetch = corpus_diff.corpus_fetcher(corpus_dir)
            report = corpus_diff.compare(fetch, fetch)
        reports.append({"corpus": label, "summary": report["summary"],
                        "rows": report["rows"]})
        _stderr_line("self-check", label, report["summary"])
        worst = max(worst, _summary_exit(report["summary"]))
    if not reports:
        worst = 2
        print("error: self-check verified NOTHING (every corpus was skipped)\n"
              "next step: `python tools/corpus.py pull`", file=sys.stderr)
    # Exactly ONE JSON document per process, whatever was selected or skipped.
    print(json.dumps({
        "mode": "self-check",
        "summary": {"corpora_checked": len(reports), "corpora_skipped": len(skipped),
                    "drift": sum(r["summary"]["drift"] for r in reports),
                    "ok": worst == 0},
        "reports": reports, "skipped": skipped,
    }, indent=1))
    return worst


def cmd_live_diff(args: argparse.Namespace) -> int:
    """Diff one corpus against a live BMC (GET-only; ``--dry-run`` = no I/O)."""
    if args.corpus_dir:
        corpus_source, label = Path(args.corpus_dir), str(args.corpus_dir)
        if not corpus_source.is_dir():
            print(f"error: --corpus-dir {corpus_source} is not a directory\n"
                  "next step: extract a corpus first "
                  "(`python tools/corpus.py extract-all`)", file=sys.stderr)
            return 2
        extracted = contextlib.nullcontext(corpus_source)
    else:
        if not (args.vendor and args.model):
            print("error: pass --vendor AND --model (or --corpus-dir)\n"
                  "next step: `python tools/corpus.py list` shows the choices",
                  file=sys.stderr)
            return 2
        row = resolve(args.vendor, args.model)
        if row is None:
            print(f"error: no corpus for {args.vendor}/{args.model}\n"
                  "next step: `python tools/corpus.py list`", file=sys.stderr)
            return 2
        reason = _corpus_ready(row)
        if reason:
            print(f"error: {reason}", file=sys.stderr)
            return 2
        extracted, label = _extracted(row), f"{row['vendor']}/{row['model']}"
    with extracted as corpus_dir:
        corpus_fetch = corpus_diff.corpus_fetcher(corpus_dir)
        # One stable document shape for BOTH dry and real runs, so a consumer
        # can always key on the same fields (plan is null on a real run;
        # summary/rows are null/empty on a dry run).
        document = {"mode": "live-diff", "dry_run": bool(args.dry_run),
                    "corpus": label, "target": args.ip,
                    "plan": None, "summary": None, "rows": []}
        if args.dry_run:
            document["plan"] = corpus_diff.plan(corpus_fetch)
            print(json.dumps(document, indent=1))
            print("dry-run: no network round-trips performed", file=sys.stderr)
            if not document["plan"]:
                print("error: the plan discovered nothing — corpus dir is empty "
                      "or lacks the ServiceRoot fixture\nnext step: "
                      "`python tools/corpus.py verify`", file=sys.stderr)
                return 2
            return 0
        username, password = _bmc_credentials(args.ip, Path(args.inventory))
        live_fetch = _live_fetcher(args.ip, username, password)
        report = corpus_diff.compare(live_fetch, corpus_fetch)
    document["summary"], document["rows"] = report["summary"], report["rows"]
    print(json.dumps(document, indent=1))
    _stderr_line("live-diff", label, report["summary"])
    code = _summary_exit(report["summary"])
    if code == 2:
        print("error: live-diff checked nothing — wrong corpus dir, empty "
              "corpus, or every live GET failed\nnext step: verify the corpus "
              "(`python tools/corpus.py verify`) and the BMC route/credentials",
              file=sys.stderr)
    return code


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse CLI."""
    parser = argparse.ArgumentParser(description="Manage the Redfish corpus library.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, needs_dest in (
        ("list", cmd_list, False),
        ("pull", cmd_pull, False),
        ("extract-all", cmd_extract_all, True),
        ("verify", cmd_verify, False),
        ("self-check", cmd_self_check, False),
    ):
        p = sub.add_parser(name, help=func.__doc__.splitlines()[0])
        p.add_argument("--vendor", help="filter to one vendor (e.g. dell)")
        p.add_argument("--model", help="filter to one model (e.g. gb300)")
        if needs_dest:
            p.add_argument("--dest", required=True, help="destination directory")
        p.set_defaults(func=func)
    live = sub.add_parser(
        "live-diff", help=cmd_live_diff.__doc__.splitlines()[0])
    live.add_argument("--vendor", help="corpus vendor (e.g. supermicro)")
    live.add_argument("--model", help="corpus model (e.g. gb300)")
    live.add_argument("--corpus-dir", help="extracted corpus dir (overrides "
                                           "--vendor/--model resolution)")
    live.add_argument("--ip", required=True, help="live BMC host or IP (GET-only)")
    live.add_argument("--inventory", default=str(DEFAULT_INVENTORY),
                      help="credentials inventory YAML (never printed); env "
                           "REDFISH_USERNAME/REDFISH_PASSWORD is the fallback")
    live.add_argument("--dry-run", action="store_true",
                      help="print the fetch plan as JSON; no network I/O")
    live.set_defaults(func=cmd_live_diff)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
