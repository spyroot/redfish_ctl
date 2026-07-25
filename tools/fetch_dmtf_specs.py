"""Fetch the DMTF Redfish standards named in the baseline manifest into spec/.

The DMTF Redfish standards (https://www.dmtf.org/standards/redfish) are public.
This tool downloads the artifacts declared in the baseline manifest
(spec/dmtf/redfish/<release>/manifest.yaml) to their ``localPath`` under the
manifest's directory, so vendoring a new standard is ONE manifest row, never a
code edit. Every download is content-verified: when the manifest's binding
block records a sha256, a mismatch fails loudly; when it does not, the computed
sha256 is printed so it can be pinned. Downloaded files land where the repo's
``*.zip``/``*.pdf`` LFS rules apply, so ``git add`` tracks them in LFS.

    python tools/fetch_dmtf_specs.py --list          # what the manifest declares + on-disk state
    python tools/fetch_dmtf_specs.py                 # fetch missing artifacts
    python tools/fetch_dmtf_specs.py --id DSP8010    # fetch one
    python tools/fetch_dmtf_specs.py --all --force    # re-fetch everything
    python tools/fetch_dmtf_specs.py --print-binding  # emit a sha256 binding block for the manifest

Audience: agent | human. Report on stdout, diagnostics on stderr, exit 0 clean,
1 on a verification failure, 2 on a usage/environment error. Downloading is a
network operation — this tool is the ONLY sanctioned network step in the repo's
spec workflow and never runs from a test.

Author Mus spyroot@gmail.com
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "spec" / "dmtf" / "redfish" / "2026.1" / "manifest.yaml"
_CHUNK = 1 << 16


def load_manifest(path: Path) -> dict:
    """Return the parsed baseline manifest.

    :param path: manifest YAML path.
    :return: the parsed manifest mapping.
    :raises SystemExit: exit 2 when the manifest is missing or unreadable.
    """
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"error: cannot read manifest {path}: {exc}\n"
              "next step: pass --manifest <path> to a DMTF baseline manifest",
              file=sys.stderr)
        raise SystemExit(2)


def _artifacts(manifest: dict) -> list[dict]:
    """Return the artifact rows that declare a download URL and localPath.

    :param manifest: parsed manifest.
    :return: artifact dicts with both ``url`` and ``localPath``.
    """
    return [a for a in manifest.get("artifacts", [])
            if a.get("url") and a.get("localPath")]


def _pinned_sha(manifest: dict, artifact_id: str) -> str | None:
    """Return the sha256 pinned for an artifact in the binding block, if any.

    :param manifest: parsed manifest.
    :param artifact_id: the artifact's ``id`` (e.g. ``DSP8010``).
    :return: the pinned hex sha256, or None when unpinned.
    """
    return ((manifest.get("binding") or {}).get("sha256") or {}).get(artifact_id)


def _sha256(path: Path) -> str:
    """Compute the sha256 of a file.

    :param path: file to hash.
    :return: lowercase hex digest.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` atomically (temp file then rename).

    :param url: source URL (DMTF public standards host).
    :param dest: destination path (parent created if needed).
    :raises SystemExit: exit 2 on any network/IO failure, with the next step.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "redfish_ctl-spec-fetch"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
        tmp.replace(dest)
    except (urllib.error.URLError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        print(f"error: download failed for {url}: {exc}\n"
              "next step: check network access to dmtf.org and retry",
              file=sys.stderr)
        raise SystemExit(2)


def _select(artifacts: list[dict], artifact_id: str | None) -> list[dict]:
    """Filter artifacts by optional id.

    :param artifacts: candidate artifact rows.
    :param artifact_id: an id to select, or None for all.
    :return: the selected rows.
    :raises SystemExit: exit 2 when a requested id is unknown.
    """
    if artifact_id is None:
        return artifacts
    hit = [a for a in artifacts if a["id"] == artifact_id]
    if not hit:
        known = ", ".join(a["id"] for a in artifacts)
        print(f"error: no artifact {artifact_id!r} in the manifest\n"
              f"next step: choose one of: {known}", file=sys.stderr)
        raise SystemExit(2)
    return hit


def cmd_list(manifest: dict, manifest_path: Path) -> int:
    """Print each declared artifact with its on-disk and pin state.

    :param manifest: parsed manifest.
    :param manifest_path: manifest path (localPaths resolve beside it).
    :return: exit 0.
    """
    base = Path(manifest_path).parent
    print(f"{'ID':<10} {'PRESENT':<8} {'PINNED':<7} {'VERIFIED':<9} LOCALPATH")
    for a in _artifacts(manifest):
        dest = base / a["localPath"]
        present = dest.exists()
        pinned = _pinned_sha(manifest, a["id"])
        verified = "-"
        if present and pinned:
            verified = "ok" if _sha256(dest) == pinned else "MISMATCH"
        print(f"{a['id']:<10} {'yes' if present else 'no':<8} "
              f"{'yes' if pinned else 'no':<7} {verified:<9} {a['localPath']}")
    return 0


def cmd_print_binding(manifest: dict, manifest_path: Path) -> int:
    """Emit a sha256 binding block for the artifacts present on disk.

    :param manifest: parsed manifest.
    :param manifest_path: manifest path (localPaths resolve beside it).
    :return: exit 0.
    """
    base = Path(manifest_path).parent
    print("binding:\n  sha256:")
    for a in _artifacts(manifest):
        dest = base / a["localPath"]
        if dest.exists():
            print(f"    {a['id']}: \"{_sha256(dest)}\"")
    return 0


def cmd_fetch(manifest: dict, manifest_path: Path,
              artifact_id: str | None, force: bool) -> int:
    """Fetch selected artifacts, verifying the sha256 when the manifest pins it.

    :param manifest: parsed manifest.
    :param manifest_path: manifest path (localPaths resolve beside it).
    :param artifact_id: a single id to fetch, or None for all declared.
    :param force: re-download even when the file already exists.
    :return: exit 0 clean, 1 when any sha256 verification failed.
    """
    base = Path(manifest_path).parent
    rows = _select(_artifacts(manifest), artifact_id)
    failed = False
    for a in rows:
        dest = base / a["localPath"]
        pinned = _pinned_sha(manifest, a["id"])
        if dest.exists() and not force:
            if pinned and _sha256(dest) != pinned:
                print(f"MISMATCH {a['id']}: on-disk sha != manifest pin "
                      "(re-fetch with --force or fix the pin)", file=sys.stderr)
                failed = True
            else:
                print(f"present  {a['id']}  {a['localPath']}")
            continue
        print(f"fetch    {a['id']}  {a['url']}", file=sys.stderr)
        _download(a["url"], dest)
        actual = _sha256(dest)
        if pinned and actual != pinned:
            print(f"MISMATCH {a['id']}: downloaded sha {actual} != pin {pinned}",
                  file=sys.stderr)
            failed = True
        elif pinned:
            print(f"ok       {a['id']}  {a['localPath']}  (sha verified)")
        else:
            print(f"ok       {a['id']}  {a['localPath']}  (unpinned; sha={actual})")
    if failed:
        print("verification FAILED — see MISMATCH lines above", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse CLI.

    :return: the configured parser.
    """
    p = argparse.ArgumentParser(
        description="Fetch DMTF Redfish standards named in the baseline manifest.")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                   help="baseline manifest (default: the 2026.1 manifest)")
    p.add_argument("--id", help="fetch/list one artifact id (e.g. DSP8010)")
    p.add_argument("--all", action="store_true",
                   help="operate on every declared artifact (the default target)")
    p.add_argument("--force", action="store_true",
                   help="re-download even when the file exists")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true",
                      help="list declared artifacts and their on-disk state")
    mode.add_argument("--print-binding", action="store_true",
                      help="emit a sha256 binding block for present artifacts")
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    :param argv: argument vector, or None to use ``sys.argv``.
    :return: process exit code (0 clean, 1 verification failure, 2 usage/env).
    """
    args = build_parser().parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.list:
        return cmd_list(manifest, args.manifest)
    if args.print_binding:
        return cmd_print_binding(manifest, args.manifest)
    return cmd_fetch(manifest, args.manifest, args.id, args.force)


if __name__ == "__main__":
    sys.exit(main())
