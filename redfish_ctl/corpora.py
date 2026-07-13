"""Canonical Redfish corpus manifest and materialization helpers."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "corpora" / "manifest.v1.json"
ARTIFACT_ALIASES = {"full": "dataset", "sim": "mock"}


@dataclass(frozen=True)
class CorpusRow:
    """One corpus archive entry from the v1 manifest."""

    id: str
    kind: str
    vendor: str
    model: str
    capture_id: str
    platform_label: str
    source: str
    archive: str
    archive_sha256: str
    archive_bytes: int
    root: str
    redfish_version: str
    json_count: int
    archive_json_count: int
    status: str
    contains_rest_api_map: bool
    contains_logs: bool
    contains_log_entries: bool
    contains_events: bool
    contains_schemas: bool
    contains_registries: bool
    sanitized: bool
    redaction_version: str
    license_note: str
    consumers: list[str]
    completeness: str
    notes: str

    @property
    def archive_path(self) -> Path:
        """Absolute archive path in the repository checkout."""
        return REPO_ROOT / self.archive

    @property
    def slug(self) -> str:
        """Stable materialized directory name for this corpus."""
        if self.kind == "dataset":
            return f"{self.vendor}_{self.model}_{self.capture_id}"
        return f"{self.vendor}_{self.model}"

    @property
    def materialized_prefix(self) -> PurePosixPath:
        """Stable POSIX prefix used by CLI file listings."""
        return PurePosixPath(self.kind) / self.slug

    @property
    def root_parts(self) -> tuple[str, ...]:
        """Archive root prefix as POSIX path parts."""
        return PurePosixPath(self.root).parts

    def asdict(self) -> dict:
        """Return a JSON-serializable manifest row."""
        return asdict(self)


@dataclass(frozen=True)
class CorpusManifest:
    """Parsed corpus manifest."""

    schema_version: int
    description: str
    corpora: list[CorpusRow]

    def asdict(self) -> dict:
        """Return a JSON-serializable manifest."""
        return {
            "schema_version": self.schema_version,
            "description": self.description,
            "corpora": [row.asdict() for row in self.corpora],
        }


def normalize_kind(kind: Optional[str]) -> Optional[str]:
    """Normalize artifact names while accepting legacy kind aliases."""
    if kind is None:
        return None
    return ARTIFACT_ALIASES.get(kind.lower(), kind.lower())


def load_manifest(path: Path = MANIFEST_PATH) -> CorpusManifest:
    """Load the canonical v1 corpus manifest."""
    data = json.loads(Path(path).read_text())
    return CorpusManifest(
        schema_version=int(data["schema_version"]),
        description=data.get("description", ""),
        corpora=[CorpusRow(**row) for row in data.get("corpora", [])],
    )


def select_rows(
    *,
    corpus_id: Optional[str] = None,
    vendor: Optional[str] = None,
    model: Optional[str] = None,
    kind: Optional[str] = "mock",
    manifest: Optional[CorpusManifest] = None,
) -> list[CorpusRow]:
    """Filter manifest rows by id, vendor, model, and artifact kind."""
    rows = list((manifest or load_manifest()).corpora)
    if corpus_id:
        wanted = corpus_id.lower()
        rows = [row for row in rows if row.id.lower() == wanted]
    if vendor:
        wanted = vendor.lower()
        rows = [row for row in rows if row.vendor.lower() == wanted]
    if model:
        wanted = model.lower()
        rows = [row for row in rows if row.model.lower() == wanted]
    normalized_kind = normalize_kind(kind)
    if normalized_kind:
        rows = [row for row in rows if row.kind.lower() == normalized_kind]
    return rows


def resolve(
    corpus_id: Optional[str] = None,
    *,
    vendor: Optional[str] = None,
    model: Optional[str] = None,
    kind: Optional[str] = "mock",
) -> Optional[CorpusRow]:
    """Resolve one corpus by id or vendor/model."""
    rows = select_rows(corpus_id=corpus_id, vendor=vendor, model=model, kind=kind)
    return rows[0] if rows else None


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_lfs_pointer(path: Path) -> bool:
    """True when a corpus archive is an unpulled Git-LFS pointer file."""
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(120)
    except OSError:
        return False
    return head.startswith(b"version https://git-lfs.github.com/spec")


def _member_relative_name(row: CorpusRow, member_name: str) -> Optional[PurePosixPath]:
    """Map a tar member under the captured root to a safe relative path."""
    if not member_name:
        return None
    path = PurePosixPath(member_name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive member path: {member_name}")
    parts = path.parts
    root_parts = row.root_parts
    if len(parts) <= len(root_parts) or parts[:len(root_parts)] != root_parts:
        return None
    return PurePosixPath(*parts[len(root_parts):])


def _is_resource_json(rel: PurePosixPath) -> bool:
    """True for Redfish payload JSON, false for corpus metadata sidecars."""
    if rel.name in {"corpus.json", "manifest.json", "manifest.v1.json", "rest_api_map.v1.json"}:
        return False
    return rel.suffix == ".json"


def iter_files(row: CorpusRow, *, include_metadata: bool = False) -> Iterable[str]:
    """Yield stable materialized file paths for one corpus archive."""
    with tarfile.open(row.archive_path) as tar:
        for member in tar:
            rel = _member_relative_name(row, member.name)
            if rel is None or not member.isfile():
                continue
            if not include_metadata and not _is_resource_json(rel):
                continue
            yield str(row.materialized_prefix / rel)


def iter_json_files(row: CorpusRow) -> Iterable[str]:
    """Yield resource JSON paths for one corpus archive."""
    yield from iter_files(row, include_metadata=False)


def materialize(
    dest: Path | str,
    *,
    corpus_id: Optional[str] = None,
    vendor: Optional[str] = None,
    model: Optional[str] = None,
    kind: Optional[str] = "mock",
    legacy_layout: bool = False,
) -> list[Path]:
    """Extract selected corpora under stable roots."""
    rows = select_rows(corpus_id=corpus_id, vendor=vendor, model=model, kind=kind)
    if not rows:
        raise ValueError("no corpora match the requested filters")

    base = Path(dest)
    outputs: list[Path] = []
    for row in rows:
        if is_lfs_pointer(row.archive_path):
            raise ValueError(f"{row.archive} is a Git-LFS pointer; run corpus pull first")
        out_root = base / row.slug if legacy_layout else base / row.kind / row.slug
        out_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(row.archive_path) as tar:
            for member in tar:
                rel = _member_relative_name(row, member.name)
                if rel is None:
                    continue
                target = out_root / Path(*rel.parts)
                resolved_root = out_root.resolve()
                resolved_target = target.resolve(strict=False)
                if resolved_root not in (resolved_target, *resolved_target.parents):
                    raise ValueError(f"unsafe archive member path: {member.name}")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise ValueError(f"unsupported archive member type: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    raise ValueError(f"could not read archive member: {member.name}")
                with src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        outputs.append(out_root)
    return outputs


def verify_rows(rows: Iterable[CorpusRow], *, require_materialized: bool = False) -> list[dict]:
    """Verify archive presence, digest, resource JSON counts, and maps."""
    results = []
    for row in rows:
        status = {
            "id": row.id,
            "kind": row.kind,
            "archive": row.archive,
            "ok": False,
            "status": "",
        }
        if not row.archive_path.exists():
            status["status"] = "missing"
            results.append(status)
            continue
        if is_lfs_pointer(row.archive_path):
            status["status"] = "pointer"
            status["ok"] = not require_materialized
            results.append(status)
            continue
        if row.archive_path.stat().st_size != row.archive_bytes:
            status["status"] = "size-mismatch"
            results.append(status)
            continue
        if sha256_file(row.archive_path) != row.archive_sha256:
            status["status"] = "sha256-mismatch"
            results.append(status)
            continue
        count = sum(1 for _ in iter_json_files(row))
        if count != row.json_count:
            status["status"] = "json-count-mismatch"
            status["actual_json_count"] = count
            results.append(status)
            continue
        if row.contains_rest_api_map:
            with tarfile.open(row.archive_path) as tar:
                names = set(tar.getnames())
            root = row.root.rstrip("/")
            if f"{root}/rest_api_map.v1.json" not in names or f"{root}/rest_api_map.npy" not in names:
                status["status"] = "map-missing"
                results.append(status)
                continue
        status["ok"] = True
        status["status"] = "ok"
        status["actual_json_count"] = count
        results.append(status)
    return results


def pull_rows(rows: Iterable[CorpusRow]) -> int:
    """Run ``git lfs pull`` for selected corpus archives."""
    includes = ",".join(row.archive for row in rows)
    if not includes:
        raise ValueError("no corpora match the requested filters")
    return subprocess.call(["git", "lfs", "pull", f"--include={includes}"], cwd=REPO_ROOT)


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True, indent=2)


def _rows_for_args(args: argparse.Namespace) -> list[CorpusRow]:
    return select_rows(
        corpus_id=getattr(args, "corpus_id", None),
        vendor=getattr(args, "vendor", None),
        model=getattr(args, "model", None),
        kind=getattr(args, "kind", "mock"),
    )


def add_filters(cmd: argparse.ArgumentParser) -> None:
    """Add common corpus selection flags to a parser."""
    cmd.add_argument("--all", action="store_true", help="select all matching corpora")
    cmd.add_argument("--id", dest="corpus_id", help="filter to one corpus id")
    cmd.add_argument("--vendor", help="filter to one vendor")
    cmd.add_argument("--model", help="filter to one model")
    cmd.add_argument(
        "--kind", "--artifact",
        choices=("mock", "dataset", "full", "sim"),
        default="mock",
        help="artifact kind: mock for simulators, dataset for IGC/analytics")


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    """Build the standalone corpus CLI parser."""
    parser = argparse.ArgumentParser(
        description="Manage Redfish corpus archives.", add_help=add_help)
    sub = parser.add_subparsers(dest="action", required=True)

    list_cmd = sub.add_parser("list", help="list corpora from the manifest")
    add_filters(list_cmd)
    list_cmd.add_argument("--format", choices=("table", "json"), default="table")

    pull_cmd = sub.add_parser("pull", help="pull selected LFS archives")
    add_filters(pull_cmd)

    verify_cmd = sub.add_parser("verify", help="verify selected archives")
    add_filters(verify_cmd)
    verify_cmd.add_argument("--format", choices=("table", "json"), default="table")
    verify_cmd.add_argument(
        "--require-materialized",
        action="store_true",
        help="fail on missing Git-LFS objects or bare pointers")

    materialize_cmd = sub.add_parser("materialize", help="extract archives by stable slug")
    add_filters(materialize_cmd)
    materialize_cmd.add_argument(
        "--dest", "--output", dest="dest", required=True,
        help="destination directory")

    extract_cmd = sub.add_parser(
        "extract-all",
        help="legacy-compatible extraction into <dest>/<vendor>_<model>")
    add_filters(extract_cmd)
    extract_cmd.add_argument("--dest", required=True, help="destination directory")

    files_cmd = sub.add_parser("files", help="list materialized resource JSON names")
    add_filters(files_cmd)
    files_cmd.add_argument("--limit", type=int, default=0, help="maximum files to list")
    files_cmd.add_argument("--format", choices=("table", "json"), default="table")
    return parser


def _print_table(action: str, data: dict) -> None:
    """Print old human-readable output for tools/corpus.py."""
    if action == "list":
        rows = data["corpora"]
        total = 0
        print(f"{'KIND':<8} {'VENDOR':<11} {'MODEL':<16} {'REDFISH':<8} {'JSON':>6}  ARCHIVE")
        for row in rows:
            total += int(row["json_count"])
            print(
                f"{row['kind']:<8} {row['vendor']:<11} {row['model']:<16} "
                f"{row['redfish_version']:<8} {row['json_count']:>6}  {row['archive']}")
        print(f"{'':<8} {'':<11} {'':<16} {'total':<8} {total:>6}")
    elif action == "verify":
        for item in data["results"]:
            prefix = "ok" if item["ok"] else item["status"]
            print(f"{prefix:<8} {item['archive']} ({item.get('actual_json_count', '?')} json)")
    elif action in {"materialize", "extract-all"}:
        for item in data["extracted"]:
            print(
                f"extracted {item['json_count']:>5} json  "
                f"{item['vendor']}/{item['model']} -> {item['output']}")
    elif action == "files":
        for item in data.get("corpora", [data]):
            print(item["id"])
            for name in item["files"]:
                print(f"  {name}")
    else:
        print(_json(data))


def dispatch(args: argparse.Namespace) -> dict:
    """Run a corpus action and return JSON-serializable data."""
    rows = _rows_for_args(args)
    if not rows:
        raise SystemExit("no corpora match the requested filters")
    if args.action == "list":
        return {
            "schema_version": load_manifest().schema_version,
            "corpora": [row.asdict() for row in rows],
        }
    if args.action == "pull":
        return {"pulled": pull_rows(rows) == 0, "corpora": [row.id for row in rows]}
    if args.action == "verify":
        results = verify_rows(
            rows,
            require_materialized=getattr(args, "require_materialized", False),
        )
        return {"ok": all(item["ok"] for item in results), "results": results}
    if args.action in {"materialize", "extract-all"}:
        legacy_layout = args.action == "extract-all"
        outputs = materialize(
            args.dest,
            corpus_id=getattr(args, "corpus_id", None),
            vendor=getattr(args, "vendor", None),
            model=getattr(args, "model", None),
            kind=getattr(args, "kind", "mock"),
            legacy_layout=legacy_layout,
        )
        return {
            "outputs": [str(path) for path in outputs],
            "extracted": [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "vendor": row.vendor,
                    "model": row.model,
                    "json_count": row.json_count,
                    "output": str(path),
                }
                for row, path in zip(rows, outputs)
            ],
        }
    if args.action == "files":
        payload = []
        for row in rows:
            files = list(iter_json_files(row))
            if args.limit and args.limit > 0:
                files = files[:args.limit]
            payload.append({"id": row.id, "kind": row.kind, "files": files})
        if len(payload) == 1:
            return payload[0]
        return {"corpora": payload}
    raise SystemExit(f"unknown corpus action: {args.action}")


def main(argv: Optional[list[str]] = None) -> int:
    """Standalone CLI entry point for ``python tools/corpus.py``."""
    args = build_parser().parse_args(argv)
    data = dispatch(args)
    if getattr(args, "format", "table") == "json":
        print(_json(data))
    else:
        _print_table(args.action, data)
    if args.action == "verify" and not data["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
