"""Behavioral coverage for the root smoke-inventory generator."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "check.sh"
TRACKED_INVENTORY = REPO_ROOT / "inventory" / "ci" / "smoke-tests.yaml"


def _copy_generator(tmp_path: Path) -> Path:
    script = tmp_path / "check.sh"
    shutil.copy2(GENERATOR, script)
    return script


def _run(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_default_is_a_non_mutating_dry_run(tmp_path):
    """No arguments report the plan without creating the inventory tree."""
    script = _copy_generator(tmp_path)

    result = _run(script)

    assert result.returncode == 0
    assert "mode=dry-run action=create" in result.stdout
    assert not (tmp_path / "inventory").exists()


def test_apply_requires_explicit_confirmation(tmp_path):
    """Apply without the named confirmation fails before writing anything."""
    script = _copy_generator(tmp_path)

    result = _run(script, "--apply")

    assert result.returncode == 2
    assert "--apply requires --confirm-smoke-inventory" in result.stderr
    assert not (tmp_path / "inventory").exists()


def test_confirmed_apply_matches_tracked_inventory_and_is_idempotent(tmp_path):
    """A confirmed create is byte-exact and a second apply is a no-op."""
    script = _copy_generator(tmp_path)
    output = tmp_path / "inventory" / "ci" / "smoke-tests.yaml"

    created = _run(script, "--apply", "--confirm-smoke-inventory")
    initial_mtime = output.stat().st_mtime_ns
    repeated = _run(script, "--apply", "--confirm-smoke-inventory")

    assert created.returncode == 0
    assert "mode=apply action=create" in created.stdout
    assert output.read_bytes() == TRACKED_INVENTORY.read_bytes()
    assert repeated.returncode == 0
    assert "mode=apply action=no-op" in repeated.stdout
    assert output.stat().st_mtime_ns == initial_mtime


def test_divergent_inventory_needs_separate_overwrite_confirmation(tmp_path):
    """Ordinary apply preserves divergent content until overwrite is confirmed."""
    script = _copy_generator(tmp_path)
    output = tmp_path / "inventory" / "ci" / "smoke-tests.yaml"
    output.parent.mkdir(parents=True)
    output.write_text("operator-owned\n", encoding="utf-8")

    refused = _run(script, "--apply", "--confirm-smoke-inventory")

    assert refused.returncode == 3
    assert output.read_text(encoding="utf-8") == "operator-owned\n"

    replaced = _run(
        script,
        "--apply",
        "--confirm-smoke-inventory",
        "--confirm-overwrite",
    )

    assert replaced.returncode == 0
    assert output.read_bytes() == TRACKED_INVENTORY.read_bytes()
    assert "action=replace" in replaced.stdout


def test_sourceable_writer_preserves_callers_exit_trap(tmp_path):
    """The writer's subshell cannot replace a sourcing caller's cleanup trap."""
    script = _copy_generator(tmp_path)
    output = tmp_path / "generated.yaml"
    marker = tmp_path / "caller-cleanup-ran"
    command = (
        f"trap 'printf preserved > {shlex.quote(str(marker))}' EXIT; "
        f"source {shlex.quote(str(script))}; "
        f"action=$(inventory_action {shlex.quote(str(output))}); "
        f"write_smoke_inventory {shlex.quote(str(output))} \"$action\""
    )

    result = subprocess.run(
        ["/bin/bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert marker.read_text(encoding="utf-8") == "preserved"
    assert output.read_bytes() == TRACKED_INVENTORY.read_bytes()
