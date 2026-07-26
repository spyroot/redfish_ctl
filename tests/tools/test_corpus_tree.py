"""Contract tests for ``tools/corpus_tree.py``.

The converter is intentionally tested through its public CLI, not private
function names.  Expected CLI shape, derived from
``specs/sim/corpus-tree-conversion.yaml``:

    python tools/corpus_tree.py plan --manifest M --vendor V --model M
    python tools/corpus_tree.py materialize --manifest M --vendor V --model M --output O
    python tools/corpus_tree.py verify --manifest M --vendor V --model M --output O

The fixtures are tiny synthetic tarballs built per test.  They never read real
Git-LFS corpora, call a live network endpoint, or mutate repository data.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "corpus_tree.py"

VENDOR = "Acme"
MODEL = "Rack1"
PROFILE_ID = "acme-rack1"
ARCNAME = "capture"
CANONICAL_CHASSIS_ROUTE = "/redfish/v1/Chassis/Chassis_0"
ALIAS_SYSTEM_ROUTE = "/redfish/v1/Systems/System_0"
CANONICAL_CHASSIS_SOURCE = "_redfish_v1_Chassis_Chassis_0.json"
ALIAS_SYSTEM_SOURCE = "_redfish_v1_Systems_System_0.json"


def _json_bytes(payload: Any) -> bytes:
    """Serialize JSON deterministically with a final newline."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _sha256(data: bytes) -> str:
    """Return the lowercase sha256 hex digest for ``data``."""
    return hashlib.sha256(data).hexdigest()


def _tarinfo(name: str, size: int = 0) -> tarfile.TarInfo:
    """Build deterministic tar metadata for one member."""
    info = tarfile.TarInfo(name)
    info.size = size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o644
    return info


def _write_tarball(
    tmp_path: Path,
    files: dict[str, bytes],
    *,
    arcname: str = ARCNAME,
    symlinks: dict[str, str] | None = None,
) -> Path:
    """Write a deterministic ``.tar.gz`` containing ``arcname/<files>``."""
    tarball = tmp_path / "corpus.tar.gz"
    with tarball.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for name, content in sorted(files.items()):
                    data = bytes(content)
                    info = _tarinfo(f"{arcname}/{name}", len(data))
                    tar.addfile(info, io.BytesIO(data))
                for name, target in sorted((symlinks or {}).items()):
                    info = _tarinfo(f"{arcname}/{name}")
                    info.type = tarfile.SYMTYPE
                    info.linkname = target
                    tar.addfile(info)
    return tarball


def _write_manifest(tmp_path: Path, tarball: Path, *, arcname: str = ARCNAME) -> Path:
    """Write a minimal manifest row pointing at the synthetic tarball."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "corpora": [
                    {
                        "vendor": VENDOR,
                        "model": MODEL,
                        "tarball": tarball.name,
                        "arcname": arcname,
                    }
                ]
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _run_corpus_tree(
    tmp_path: Path,
    command: str,
    manifest: Path,
    output: Path | None = None,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    """Run the planned CLI with a repo import path and test-local cwd."""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) if not existing else f"{REPO_ROOT}{os.pathsep}{existing}"
    args = [
        sys.executable,
        str(TOOL),
        command,
        "--manifest",
        str(manifest),
        "--vendor",
        VENDOR,
        "--model",
        MODEL,
    ]
    if output is not None:
        args.extend(["--output", str(output)])
    args.extend(extra_args)
    return subprocess.run(
        args,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _stdout_report(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """Parse a JSON report from stdout with useful assertion context."""
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - assertion helper
        raise AssertionError(
            f"stdout was not a JSON report\nstdout={proc.stdout}\nstderr={proc.stderr}"
        ) from exc


def _report_file(output: Path) -> Path:
    """Path to the materialized conversion report for the synthetic profile."""
    return output / f"{PROFILE_ID}.conversion.json"


def _profile_root(output: Path) -> Path:
    """Path to the materialized synthetic profile root."""
    return output / PROFILE_ID


def _source_names(rows: list[dict[str, Any]]) -> set[str]:
    """Source fixture basenames from report rows."""
    names = set()
    for row in rows:
        names.update(
            Path(str(source)).name
            for source in row.get("sourceFixtures", [])
        )
        source = (
            row.get("sourceFixture")
            or row.get("source")
            or row.get("file")
            or ""
        )
        if source:
            names.add(Path(str(source)).name)
    return names


def _row_for_source(rows: list[dict[str, Any]], source_fixture: str) -> dict[str, Any]:
    """Find one report row by source fixture basename."""
    for row in rows:
        source = row.get("sourceFixture") or row.get("source") or row.get("file") or ""
        if Path(str(source)).name == source_fixture:
            return row
    raise AssertionError(f"no report row for {source_fixture}: {rows!r}")


def _assert_accounting_balances(report: dict[str, Any]) -> None:
    """Check the conversion accountability equation."""
    counts = report["counts"]
    assert counts["inputRegularFiles"] == (
        counts["emittedFiles"]
        + counts["excludedFiles"]
        + counts["unresolvedFiles"]
        + counts["sidecarFiles"]
    )


def _corpus_routes(routes: dict[str, str]) -> bytes:
    """Build the explicit route authority used by the conversion contract."""
    return _json_bytes(
        {
            "schema": "redfish_ctl.corpus_routes/v1",
            "routes": [
                {"route": route, "sourceFixture": source}
                for route, source in sorted(routes.items())
            ],
        }
    )


def _status_sidecar(routes: dict[str, str]) -> bytes:
    """Build route-adjacent HTTP metadata that is not route authority."""
    return _json_bytes(
        {
            "http_status_mapping": {route: 200 for route in routes},
            "error_file_mapping": {},
        }
    )


def _happy_files() -> dict[str, bytes]:
    """A tiny Redfish tree with service root, BIOS Settings, and underscores."""
    routes = {
        "/redfish/v1": "_redfish_v1.json",
        "/redfish/v1/Systems": "_redfish_v1_Systems.json",
        "/redfish/v1/Systems/HGX_Baseboard_0": "_redfish_v1_Systems_HGX_Baseboard_0.json",
        "/redfish/v1/Systems/HGX_Baseboard_0/Bios": (
            "_redfish_v1_Systems_HGX_Baseboard_0_Bios.json"
        ),
        "/redfish/v1/Systems/HGX_Baseboard_0/Bios/Settings": (
            "_redfish_v1_Systems_HGX_Baseboard_0_Bios_Settings.json"
        ),
    }
    settings_bytes = (
        b'{\n'
        b'  "@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0/Bios/Settings",\n'
        b'  "Attributes": {"BootMode": "Uefi", "AdminName": "kept-byte-for-byte"}\n'
        b'}\n'
    )
    return {
        "corpus_routes.json": _corpus_routes(routes),
        "rest_api_map.status.json": _status_sidecar(routes),
        "rest_api_map.npy": b"legacy sidecar must not be loaded\n",
        "_redfish_v1.json": _json_bytes(
            {
                "@odata.id": "/redfish/v1",
                "Systems": {"@odata.id": "/redfish/v1/Systems"},
            }
        ),
        "_redfish_v1_Systems.json": _json_bytes(
            {
                "@odata.id": "/redfish/v1/Systems",
                "Members": [
                    {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
                ],
            }
        ),
        "_redfish_v1_Systems_HGX_Baseboard_0.json": _json_bytes(
            {
                "@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0",
                "Bios": {
                    "@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0/Bios",
                },
            }
        ),
        "_redfish_v1_Systems_HGX_Baseboard_0_Bios.json": _json_bytes(
            {
                "@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0/Bios",
                "Settings": {
                    "@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0/Bios/Settings",
                },
            }
        ),
        "_redfish_v1_Systems_HGX_Baseboard_0_Bios_Settings.json": settings_bytes,
    }


def _happy_manifest(tmp_path: Path) -> Path:
    """Create the happy synthetic tarball and return its manifest path."""
    tarball = _write_tarball(tmp_path, _happy_files())
    return _write_manifest(tmp_path, tarball)


def _canonical_alias_files(alias_payload: bytes | None = None) -> dict[str, bytes]:
    """Build a graph where Systems has an alias for a canonical Chassis resource."""
    routes = {
        "/redfish/v1": "_redfish_v1.json",
        "/redfish/v1/Systems": "_redfish_v1_Systems.json",
        ALIAS_SYSTEM_ROUTE: ALIAS_SYSTEM_SOURCE,
        "/redfish/v1/Chassis": "_redfish_v1_Chassis.json",
        CANONICAL_CHASSIS_ROUTE: CANONICAL_CHASSIS_SOURCE,
    }
    canonical_payload = (
        b'{\n'
        b'  "@odata.id": "/redfish/v1/Chassis/Chassis_0",\n'
        b'  "Id": "Chassis_0",\n'
        b'  "Links": {"ComputerSystems": [{"@odata.id": "/redfish/v1/Systems/System_0"}]},\n'
        b'  "Name": "Canonical chassis"\n'
        b'}\n'
    )
    return {
        "corpus_routes.json": _corpus_routes(routes),
        "rest_api_map.status.json": _status_sidecar(routes),
        "rest_api_map.npy": b"legacy sidecar must not be loaded\n",
        "_redfish_v1.json": _json_bytes(
            {
                "@odata.id": "/redfish/v1",
                "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
                "Systems": {"@odata.id": "/redfish/v1/Systems"},
            }
        ),
        "_redfish_v1_Systems.json": _json_bytes(
            {
                "@odata.id": "/redfish/v1/Systems",
                "Members": [{"@odata.id": ALIAS_SYSTEM_ROUTE}],
            }
        ),
        "_redfish_v1_Chassis.json": _json_bytes(
            {
                "@odata.id": "/redfish/v1/Chassis",
                "Members": [{"@odata.id": CANONICAL_CHASSIS_ROUTE}],
            }
        ),
        CANONICAL_CHASSIS_SOURCE: canonical_payload,
        ALIAS_SYSTEM_SOURCE: alias_payload
        or (
            b'{"Name":"Canonical chassis","Links":{"ComputerSystems":[{"@odata.id":'
            b'"/redfish/v1/Systems/System_0"}]},"Id":"Chassis_0","@odata.id":'
            b'"/redfish/v1/Chassis/Chassis_0"}\n'
        ),
    }


def _assert_canonical_alias_subset_report(report: dict[str, Any]) -> None:
    """Check the canonical-alias-subset exclusion details."""
    assert report["result"] == "pass"
    mappings = {row["route"]: row for row in report["mappings"]}
    assert CANONICAL_CHASSIS_ROUTE in mappings
    assert ALIAS_SYSTEM_ROUTE not in mappings
    assert Path(str(mappings[CANONICAL_CHASSIS_ROUTE]["sourceFixture"])).name == (
        CANONICAL_CHASSIS_SOURCE
    )

    alias_row = _row_for_source(report["excluded"], ALIAS_SYSTEM_SOURCE)
    assert alias_row["route"] == ALIAS_SYSTEM_ROUTE
    assert alias_row["reason"] == "canonical-alias-subset"
    assert alias_row["canonicalRoute"] == CANONICAL_CHASSIS_ROUTE
    assert alias_row["canonicalSourceFixture"] == CANONICAL_CHASSIS_SOURCE
    _assert_accounting_balances(report)


def test_materialize_maps_redfish_paths_recursively_and_preserves_payload_bytes(
    tmp_path: Path,
) -> None:
    """Service root, recursive BIOS Settings, and underscore segments map correctly."""
    manifest = _happy_manifest(tmp_path)
    output = tmp_path / "out"

    proc = _run_corpus_tree(tmp_path, "materialize", manifest, output)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    root = _profile_root(output)
    assert (root / "index.json").read_bytes() == _happy_files()["_redfish_v1.json"]
    settings = root / "Systems" / "HGX_Baseboard_0" / "Bios" / "Settings" / "index.json"
    assert settings.read_bytes() == _happy_files()[
        "_redfish_v1_Systems_HGX_Baseboard_0_Bios_Settings.json"
    ]
    assert not (root / "Systems" / "HGX" / "Baseboard" / "0").exists()

    report = json.loads(_report_file(output).read_text(encoding="utf-8"))
    mappings = {row["route"]: row for row in report["mappings"]}
    assert report["result"] == "pass"
    assert report["profileId"] == PROFILE_ID
    assert set(mappings) == {
        "/redfish/v1",
        "/redfish/v1/Systems",
        "/redfish/v1/Systems/HGX_Baseboard_0",
        "/redfish/v1/Systems/HGX_Baseboard_0/Bios",
        "/redfish/v1/Systems/HGX_Baseboard_0/Bios/Settings",
    }
    assert mappings["/redfish/v1"]["sha256"] == _sha256(_happy_files()["_redfish_v1.json"])
    assert mappings["/redfish/v1/Systems/HGX_Baseboard_0/Bios/Settings"][
        "sha256"
    ] == _sha256(_happy_files()["_redfish_v1_Systems_HGX_Baseboard_0_Bios_Settings.json"])


def test_plan_reports_routes_without_mutating_output_tree(tmp_path: Path) -> None:
    """The read-only plan command emits a report but creates no output tree."""
    manifest = _happy_manifest(tmp_path)
    output = tmp_path / "planned-output"

    proc = _run_corpus_tree(tmp_path, "plan", manifest)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not output.exists(), "plan must not create a profile tree or report file"
    report = _stdout_report(proc)
    assert report["result"] == "pass"
    assert {row["route"] for row in report["mappings"]} >= {
        "/redfish/v1",
        "/redfish/v1/Systems/HGX_Baseboard_0/Bios/Settings",
    }
    assert report["treeSha256"]


def test_corpus_routes_and_top_level_odata_id_disagreement_blocks_plan(
    tmp_path: Path,
) -> None:
    """A corpus_routes row and top-level @odata.id for one fixture must agree."""
    files = {
        "_redfish_v1.json": _json_bytes({"@odata.id": "/redfish/v1"}),
        "_redfish_v1_Systems_System_0.json": _json_bytes(
            {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"}
        ),
        "corpus_routes.json": _corpus_routes(
            {
                "/redfish/v1": "_redfish_v1.json",
                "/redfish/v1/Systems/System_0": "_redfish_v1_Systems_System_0.json",
            }
        ),
    }
    manifest = _write_manifest(tmp_path, _write_tarball(tmp_path, files))

    proc = _run_corpus_tree(tmp_path, "plan", manifest)

    assert proc.returncode != 0
    report = _stdout_report(proc)
    assert report["result"] == "fail"
    assert _source_names(report["conflicts"]) == {"_redfish_v1_Systems_System_0.json"}
    assert "disagree" in (proc.stdout + proc.stderr).lower() or report["conflicts"]


def test_canonical_alias_subset_excludes_equal_systems_alias(tmp_path: Path) -> None:
    """A parsed-equal Systems alias is excluded while the Chassis canonical emits."""
    files = _canonical_alias_files()
    assert files[CANONICAL_CHASSIS_SOURCE] != files[ALIAS_SYSTEM_SOURCE]
    assert json.loads(files[CANONICAL_CHASSIS_SOURCE]) == json.loads(
        files[ALIAS_SYSTEM_SOURCE]
    )
    manifest = _write_manifest(tmp_path, _write_tarball(tmp_path, files))
    output = tmp_path / "out"

    plan = _run_corpus_tree(tmp_path, "plan", manifest)

    assert plan.returncode == 0, plan.stdout + plan.stderr
    _assert_canonical_alias_subset_report(_stdout_report(plan))

    materialized = _run_corpus_tree(tmp_path, "materialize", manifest, output)

    assert materialized.returncode == 0, materialized.stdout + materialized.stderr
    report = json.loads(_report_file(output).read_text(encoding="utf-8"))
    _assert_canonical_alias_subset_report(report)

    root = _profile_root(output)
    canonical = root / "Chassis" / "Chassis_0" / "index.json"
    assert canonical.read_bytes() == files[CANONICAL_CHASSIS_SOURCE]
    assert not (root / "Systems" / "System_0").exists()


def test_canonical_alias_subset_different_payload_blocks_plan(tmp_path: Path) -> None:
    """A Systems alias with a different parsed payload remains blocking."""
    different_alias = (
        b'{"Name":"Different alias","Links":{"ComputerSystems":[{"@odata.id":'
        b'"/redfish/v1/Systems/System_0"}]},"Id":"Chassis_0","@odata.id":'
        b'"/redfish/v1/Chassis/Chassis_0"}\n'
    )
    files = _canonical_alias_files(alias_payload=different_alias)
    assert json.loads(files[CANONICAL_CHASSIS_SOURCE]) != json.loads(
        files[ALIAS_SYSTEM_SOURCE]
    )
    manifest = _write_manifest(tmp_path, _write_tarball(tmp_path, files))

    proc = _run_corpus_tree(tmp_path, "plan", manifest)

    assert proc.returncode != 0
    report = _stdout_report(proc)
    assert report["result"] == "fail"
    blocking_sources = _source_names(report["conflicts"]) | _source_names(report["unresolved"])
    assert ALIAS_SYSTEM_SOURCE in blocking_sources
    excluded_alias = [
        row
        for row in report["excluded"]
        if Path(str(row.get("sourceFixture", ""))).name == ALIAS_SYSTEM_SOURCE
    ]
    assert not any(row.get("reason") == "canonical-alias-subset" for row in excluded_alias)
    _assert_accounting_balances(report)


def test_malformed_query_traversal_and_fragment_routes_are_accounted(
    tmp_path: Path,
) -> None:
    """Fragments are excluded, while query and malformed/traversal routes block."""
    files = {
        "_redfish_v1.json": _json_bytes({"@odata.id": "/redfish/v1"}),
        "_redfish_v1_Fragment.json": _json_bytes(
            {"@odata.id": "/redfish/v1/Chassis/1/Assembly#/Assemblies/0"}
        ),
        "_redfish_v1_Query.json": _json_bytes(
            {"@odata.id": "/redfish/v1/Systems?$select=Id"}
        ),
        "_redfish_v1_BadPercent.json": _json_bytes(
            {"@odata.id": "/redfish/v1/Systems/%ZZ"}
        ),
        "_redfish_v1_DotDot.json": _json_bytes(
            {"@odata.id": "/redfish/v1/Systems/../Managers"}
        ),
        "_redfish_v1_EncodedSlash.json": _json_bytes(
            {"@odata.id": "/redfish/v1/Systems/%2Fslash"}
        ),
    }
    manifest = _write_manifest(tmp_path, _write_tarball(tmp_path, files))

    proc = _run_corpus_tree(tmp_path, "plan", manifest)

    assert proc.returncode != 0
    report = _stdout_report(proc)
    assert report["result"] == "fail"
    assert "_redfish_v1_Fragment.json" in _source_names(report["excluded"])
    assert {
        "_redfish_v1_Query.json",
        "_redfish_v1_BadPercent.json",
        "_redfish_v1_DotDot.json",
        "_redfish_v1_EncodedSlash.json",
    } <= _source_names(report["unresolved"])


def test_archive_traversal_and_symlink_members_are_rejected(tmp_path: Path) -> None:
    """Unsafe tar members fail closed and do not create the requested output tree."""
    files = {
        "_redfish_v1.json": _json_bytes({"@odata.id": "/redfish/v1"}),
        "../escape.json": b"{}",
    }
    tarball = _write_tarball(
        tmp_path,
        files,
        symlinks={"_redfish_v1_Link.json": "_redfish_v1.json"},
    )
    manifest = _write_manifest(tmp_path, tarball)
    output = tmp_path / "out"

    proc = _run_corpus_tree(tmp_path, "plan", manifest)

    assert proc.returncode != 0
    assert not output.exists()
    combined = (proc.stdout + proc.stderr).lower()
    assert "traversal" in combined or "symlink" in combined or "unsafe" in combined


def test_duplicate_route_and_case_fold_destination_collisions_block_plan(
    tmp_path: Path,
) -> None:
    """Duplicate routes and case-folded destinations are blocking conflicts."""
    files = {
        "_redfish_v1.json": _json_bytes({"@odata.id": "/redfish/v1"}),
        "_redfish_v1_Systems_A.json": _json_bytes(
            {"@odata.id": "/redfish/v1/Systems/A"}
        ),
        "_redfish_v1_Systems_A_copy.json": _json_bytes(
            {"@odata.id": "/redfish/v1/Systems/A"}
        ),
        "_redfish_v1_Managers_BMC.json": _json_bytes(
            {"@odata.id": "/redfish/v1/Managers/BMC"}
        ),
        "_redfish_v1_Managers_bmc.json": _json_bytes(
            {"@odata.id": "/redfish/v1/Managers/bmc"}
        ),
    }
    manifest = _write_manifest(tmp_path, _write_tarball(tmp_path, files))

    proc = _run_corpus_tree(tmp_path, "plan", manifest)

    assert proc.returncode != 0
    report = _stdout_report(proc)
    assert report["result"] == "fail"
    conflict_sources = _source_names(report["conflicts"])
    assert {"_redfish_v1_Systems_A.json", "_redfish_v1_Systems_A_copy.json"} <= (
        conflict_sources
    )
    assert {"_redfish_v1_Managers_BMC.json", "_redfish_v1_Managers_bmc.json"} <= (
        conflict_sources
    )


def test_materialize_refuses_preexisting_file_directory_collision(tmp_path: Path) -> None:
    """A destination file where a route needs a directory is a blocking collision."""
    manifest = _happy_manifest(tmp_path)
    output = tmp_path / "out"
    root = _profile_root(output)
    root.mkdir(parents=True)
    (root / "Systems").write_text("not a directory\n", encoding="utf-8")

    proc = _run_corpus_tree(tmp_path, "materialize", manifest, output)

    assert proc.returncode != 0
    combined = (proc.stdout + proc.stderr).lower()
    assert "collision" in combined or "not a directory" in combined or "systems" in combined


def test_materialize_report_is_deterministic_and_verify_detects_tamper(
    tmp_path: Path,
) -> None:
    """Repeated conversions are byte-stable, and verify catches modified output."""
    manifest = _happy_manifest(tmp_path)
    out_a = tmp_path / "out-a"
    out_b = tmp_path / "out-b"

    first = _run_corpus_tree(tmp_path, "materialize", manifest, out_a)
    second = _run_corpus_tree(tmp_path, "materialize", manifest, out_b)

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    report_a = _report_file(out_a).read_bytes()
    report_b = _report_file(out_b).read_bytes()
    assert report_a == report_b
    assert json.loads(report_a)["treeSha256"] == json.loads(report_b)["treeSha256"]

    verify_ok = _run_corpus_tree(tmp_path, "verify", manifest, out_a)
    assert verify_ok.returncode == 0, verify_ok.stdout + verify_ok.stderr
    assert _stdout_report(verify_ok)["result"] == "pass"

    (_profile_root(out_a) / "index.json").write_bytes(
        _json_bytes({"@odata.id": "/redfish/v1", "tampered": True})
    )
    verify_bad = _run_corpus_tree(tmp_path, "verify", manifest, out_a)
    assert verify_bad.returncode != 0
    combined = (verify_bad.stdout + verify_bad.stderr).lower()
    assert "hash" in combined or "tamper" in combined or "mismatch" in combined
