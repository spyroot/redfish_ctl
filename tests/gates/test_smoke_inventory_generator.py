"""Behavioral coverage for the root smoke-inventory generator."""

from __future__ import annotations

import json
import os
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


def _run(
    script: Path,
    *arguments: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_help_needs_no_external_commands(tmp_path):
    """Help succeeds through Bash even when PATH contains no tools."""
    script = _copy_generator(tmp_path)

    result = subprocess.run(
        ["/bin/bash", str(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": ""},
    )

    assert result.returncode == 0
    assert "usage: check.sh" in result.stdout


def test_default_and_explicit_dry_run_do_not_mutate(tmp_path):
    """Both dry-run forms report create without making the inventory tree."""
    script = _copy_generator(tmp_path)

    default = _run(script)
    explicit = _run(script, "--dry-run")

    assert default.returncode == 0
    assert explicit.returncode == 0
    assert "mode=dry-run action=create" in default.stdout
    assert "mode=dry-run action=create" in explicit.stdout
    assert not (tmp_path / "inventory").exists()


def test_conflicting_modes_fail_before_mutation(tmp_path):
    """Dry-run and apply cannot be selected together."""
    script = _copy_generator(tmp_path)

    result = _run(script, "--dry-run", "--apply")

    assert result.returncode == 2
    assert "choose exactly one" in result.stderr
    assert not (tmp_path / "inventory").exists()


def test_json_dry_run_is_machine_readable(tmp_path):
    """JSON mode emits a stable result object and preserves dry-run safety."""
    script = _copy_generator(tmp_path)

    result = _run(script, "--log-format", "json", "--run-id", "test-1")

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "mode": "dry-run",
        "action": "create",
        "path": str(tmp_path / "inventory" / "ci" / "smoke-tests.yaml"),
        "run_id": "test-1",
    }
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


def test_divergent_inventory_is_never_overwritten(tmp_path):
    """Confirmed apply preserves a divergent operator-owned target."""
    script = _copy_generator(tmp_path)
    output = tmp_path / "inventory" / "ci" / "smoke-tests.yaml"
    output.parent.mkdir(parents=True)
    output.write_text("operator-owned\n", encoding="utf-8")

    result = _run(script, "--apply", "--confirm-smoke-inventory")

    assert result.returncode == 3
    assert "refusing to overwrite" in result.stderr
    assert output.read_text(encoding="utf-8") == "operator-owned\n"


def test_concurrent_create_cannot_overwrite_the_winner(tmp_path):
    """A target appearing after planning is preserved instead of replaced."""
    script = _copy_generator(tmp_path)
    output = tmp_path / "generated.yaml"
    command = (
        f"source {shlex.quote(str(script))}; "
        f"action=$(inventory_action {shlex.quote(str(output))}); "
        f"printf raced > {shlex.quote(str(output))}; "
        f"if write_smoke_inventory {shlex.quote(str(output))} \"$action\"; "
        "then exit 99; fi; "
        f"[[ $(< {shlex.quote(str(output))}) == raced ]]"
    )

    result = subprocess.run(
        ["/bin/bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output.read_text(encoding="utf-8") == "raced"


def test_non_regular_target_fails_closed(tmp_path):
    """A directory at the output path is never replaced."""
    script = _copy_generator(tmp_path)
    output = tmp_path / "inventory" / "ci" / "smoke-tests.yaml"
    output.mkdir(parents=True)

    result = _run(script)

    assert result.returncode == 1
    assert "not a regular file" in result.stderr
    assert output.is_dir()


def test_sourceable_writer_preserves_callers_signal_traps(tmp_path):
    """The writer's subshell cannot replace a sourcing caller's traps."""
    script = _copy_generator(tmp_path)
    output = tmp_path / "generated.yaml"
    marker = tmp_path / "caller-cleanup-ran"
    command = (
        f"trap 'printf preserved > {shlex.quote(str(marker))}' EXIT; "
        "trap ':' INT TERM; before=$(trap -p EXIT INT TERM); "
        f"source {shlex.quote(str(script))}; "
        f"action=$(inventory_action {shlex.quote(str(output))}); "
        f"write_smoke_inventory {shlex.quote(str(output))} \"$action\"; "
        "after=$(trap -p EXIT INT TERM); [[ \"$before\" == \"$after\" ]]"
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


def test_term_cleans_partial_output(tmp_path):
    """TERM during apply removes the temporary file and leaves no target."""
    script = _copy_generator(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_chmod = fake_bin / "chmod"
    fake_chmod.write_text("#!/bin/sh\nkill -TERM \"$PPID\"\n", encoding="utf-8")
    fake_chmod.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"

    result = _run(
        script,
        "--apply",
        "--confirm-smoke-inventory",
        env=env,
    )
    output = tmp_path / "inventory" / "ci" / "smoke-tests.yaml"

    assert result.returncode != 0
    assert not output.exists()
    assert list(output.parent.glob("smoke-tests.yaml.tmp.*")) == []


def test_independent_readback_rejects_corrupt_move(tmp_path):
    """A write that does not match the canonical bytes fails after read-back."""
    script = _copy_generator(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        "#!/bin/sh\nprintf corrupt > \"$4\"\nrm -f -- \"$3\"\n",
        encoding="utf-8",
    )
    fake_mv.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"

    result = _run(
        script,
        "--apply",
        "--confirm-smoke-inventory",
        env=env,
    )

    assert result.returncode == 5
    assert "read-back mismatch" in result.stderr
