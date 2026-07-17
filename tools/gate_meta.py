"""Meta-gate: enforce that the gate registry and pipeline stay internally consistent.

Reads ``gates.yaml`` (the single registry of every mandatory gate) and fails the
build when the pipeline could silently skip, misroute, or mis-classify a gate.
It is itself registered as the ``repo.meta`` gate and is run in CI via
``tests/test_gate_meta.py``. Checks (a check whose inputs do not exist yet — no
``.gitlab-ci.yml``, no ``modules/`` — is reported as skipped, not failed):

    1. every required gate's command file exists
    2. every required gate's command file is executable
    3. every mandatory gate ID is present in the registry
    4. no GitLab job uses ``allow_failure: true``
    5. every GitLab CI job carries the ``homelab-k8s`` runner tag
    6. every module exposes validate/plan/apply/verify/rollback
    7. any module (or gate) that applies also has verify AND rollback
    8. no live-apply (``mutates: true``) job is reachable in a merge-request pipeline

    python tools/gate_meta.py            # exit 0 = consistent, 1 = a check failed
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ("validate", "plan", "apply", "verify", "rollback")


def _load_registry() -> dict:
    """Load ``gates/manifest.yaml`` and validate it against its JSON schema.

    :return: the parsed registry mapping.
    :raises ValueError: when the file is missing, unparseable, or schema-invalid.
    """
    import json

    import yaml

    path = REPO_ROOT / "gates" / "manifest.yaml"
    if not path.is_file():
        raise ValueError("gates/manifest.yaml is missing")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("gates"), list):
        raise ValueError("gates/manifest.yaml must be a mapping with a 'gates' list")
    schema_path = REPO_ROOT / "schemas" / "gates.schema.json"
    if schema_path.is_file():
        try:
            import jsonschema
        except ImportError:
            jsonschema = None  # schema check also runs as the repo.schemas gate
        if jsonschema is not None:
            try:
                jsonschema.validate(data, json.loads(schema_path.read_text(encoding="utf-8")))
            except jsonschema.ValidationError as exc:
                raise ValueError(
                    f"gates/manifest.yaml fails gates.schema.json: {exc.message}") from exc
    ids = [g.get("id") for g in data["gates"]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate gate ids: {dupes}")
    return data


def _check_commands(registry: dict) -> list[str]:
    """Checks 1 & 2: required gate commands exist and are executable.

    :param registry: the parsed gate registry.
    :return: list of failure messages.
    """
    failures: list[str] = []
    for gate in registry["gates"]:
        if not gate.get("required", False):
            continue
        gate_id = gate.get("id", "<no-id>")
        command = gate.get("command")
        if not command:
            failures.append(f"gate {gate_id}: no command")
            continue
        path = REPO_ROOT / command
        if not path.is_file():
            failures.append(f"gate {gate_id}: command does not exist: {command}")
        elif not (path.stat().st_mode & 0o111):
            failures.append(f"gate {gate_id}: command is not executable: {command}")
    return failures


def _check_mandatory_ids(registry: dict) -> list[str]:
    """Check 3: every mandatory ID appears in the registry AND is required (not optional).

    :param registry: the parsed gate registry.
    :return: list of failure messages.
    """
    by_id = {g.get("id"): g for g in registry["gates"]}
    failures = []
    for mid in registry.get("mandatory_ids", []):
        if mid not in by_id:
            failures.append(f"mandatory gate ID absent from registry: {mid}")
        elif not by_id[mid].get("required", False):
            failures.append(f"mandatory gate {mid} is registered as optional (required:false)")
    return failures


def _check_no_unregistered_scripts(registry: dict) -> list[str]:
    """Every gate script under scripts/gates/ must be registered (no orphan/unregistered gate).

    :param registry: the parsed gate registry.
    :return: list of failure messages.
    """
    registered = {g.get("command") for g in registry["gates"]}
    gates_dir = REPO_ROOT / "scripts" / "gates"
    if not gates_dir.is_dir():
        return []
    failures = []
    for script in sorted(gates_dir.rglob("*.sh")):
        rel = script.relative_to(REPO_ROOT).as_posix()
        if rel == "scripts/gates/run.sh":
            continue  # the runner is infrastructure, not a gate
        if rel not in registered:
            failures.append(f"unregistered gate script (not in the registry): {rel}")
    return failures


def _check_modules() -> tuple[list[str], bool]:
    """Checks 6 & 7: modules expose the full lifecycle; apply implies verify+rollback.

    :return: (failures, ran) — ran is False when there is no ``modules/`` tree.
    """
    modules_dir = REPO_ROOT / "modules"
    if not modules_dir.is_dir():
        return [], False
    failures: list[str] = []
    for module in sorted(p for p in modules_dir.iterdir() if p.is_dir()):
        scripts = module / "scripts"
        have = {name for name in LIFECYCLE if (scripts / f"{name}.sh").is_file()}
        missing = [n for n in LIFECYCLE if n not in have]
        if missing:
            failures.append(f"module {module.name}: missing lifecycle scripts: {missing}")
        elif "apply" in have and not {"verify", "rollback"} <= have:
            failures.append(f"module {module.name}: apply without verify+rollback")
    return failures, True


def _check_gitlab(registry: dict) -> tuple[list[str], bool]:
    """Checks 4, 5 & 8 against ``.gitlab-ci.yml`` when it exists.

    :param registry: the parsed gate registry (for the required runner tag).
    :return: (failures, ran) — ran is False when there is no ``.gitlab-ci.yml``.
    """
    import yaml

    path = REPO_ROOT / ".gitlab-ci.yml"
    if not path.is_file():
        return [], False
    ci = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    runner_tag = registry.get("runner_tag", "homelab-k8s")
    failures: list[str] = []
    real_jobs = {}
    for name, job in ci.items():
        if not isinstance(job, dict) or name.startswith(".") or "script" not in job:
            continue  # not a real job (global keys, templates, hidden jobs)
        real_jobs[name] = job
        if job.get("allow_failure") is True:
            failures.append(f"gitlab job {name}: allow_failure:true is forbidden")
        if runner_tag not in (job.get("tags") or []):
            failures.append(f"gitlab job {name}: missing runner tag '{runner_tag}'")
        # A live-apply/deploy job must not run in a merge-request pipeline.
        text = repr(job.get("rules")) + repr(job.get("only"))
        looks_apply = ("apply" in name or "deploy" in name or job.get("mutates") is True)
        if looks_apply and "merge_request" in text:
            failures.append(f"gitlab job {name}: live-apply reachable in a merge-request pipeline")
    for required in registry.get("required_jobs", []):
        if required not in real_jobs:
            failures.append(f"required GitLab job missing: {required}")
    return failures, True


def run() -> tuple[bool, list[str], list[str]]:
    """Run every meta-gate check.

    :return: (ok, failures, skipped) — ok is True when there are no failures.
    """
    registry = _load_registry()
    failures = (_check_commands(registry) + _check_mandatory_ids(registry)
                + _check_no_unregistered_scripts(registry))
    skipped: list[str] = []
    mod_fail, mod_ran = _check_modules()
    failures += mod_fail
    if not mod_ran:
        skipped.append("modules/ (no module tree yet)")
    gl_fail, gl_ran = _check_gitlab(registry)
    failures += gl_fail
    if not gl_ran:
        skipped.append(".gitlab-ci.yml (not present yet)")
    return (not failures, failures, skipped)


def main() -> int:
    """CLI entry: print the report and return the process exit code.

    :return: 0 when consistent, 1 when a check failed.
    """
    ok, failures, skipped = run()
    for message in failures:
        print(f"META-GATE FAIL: {message}", file=sys.stderr)
    for note in skipped:
        print(f"meta-gate: skipped {note}")
    print("meta-gate: OK" if ok else f"meta-gate: {len(failures)} failure(s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
