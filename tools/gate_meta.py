"""Meta-gate: enforce that the gate registry and pipeline stay internally consistent.

Reads ``gates/manifest.yaml`` (the single registry of every mandatory gate) and
fails when registry, command, pipeline, runner, or mutation-safety contracts can
silently drift. It is registered as ``meta.gate-registry`` and covered by
``tests/gates/``. Checks whose optional inputs do not exist yet are reported as
skipped rather than failed.

    python tools/gate_meta.py            # exit 0 = consistent, 1 = a check failed
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ("validate", "plan", "apply", "verify", "rollback")

# GitLab's reserved top-level keywords. Everything else at the top level of .gitlab-ci.yml is a job,
# whether or not it declares an inline ``script`` — so the job checks must exclude these by NAME and
# never by the presence of a job keyword.
GITLAB_GLOBAL_KEYS = frozenset({
    "default", "include", "stages", "variables", "workflow", "spec",
    "image", "services", "before_script", "after_script", "cache", "types",
})
SMOKE_CLASSES = frozenset({
    "wiring", "offline-component", "ephemeral-integration",
    "protected-live", "recovery", "status-reflection",
})
PROJECT_CI_SELECTOR_DENY = (
    '($FOCUSED_GATE != null && $FOCUSED_GATE != "") || '
    '$MERGE_PROFILE == "merge" || '
    '(($PROJECT_CI_PROFILE != null && $PROJECT_CI_PROFILE != "") && '
    '$PROJECT_CI_PROFILE != "protected") || '
    '($PROJECT_CI_SMOKE != null && $PROJECT_CI_SMOKE != "")'
)


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
    """Checks 1 & 2: every gate command exists and is executable.

    Optional gates are checked too. ``required: false`` used to skip the check, so a registry row could
    name a missing or non-executable path that only blew up when the profile actually ran in CI.

    :param registry: the parsed gate registry.
    :return: list of failure messages.
    """
    failures: list[str] = []
    for gate in registry["gates"]:
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


def _real_gitlab_jobs(ci: dict) -> dict:
    """Return analyzable top-level GitLab jobs from a parsed pipeline.

    :param ci: parsed ``.gitlab-ci.yml`` mapping.
    :return: real job mappings, excluding global keywords and hidden templates.
    """
    return {
        name: job
        for name, job in ci.items()
        if name not in GITLAB_GLOBAL_KEYS
        and isinstance(job, dict)
        and not name.startswith(".")
    }


def _script_lines(job: dict) -> list[str]:
    """Normalize a GitLab job's script into individual command strings.

    :param job: parsed GitLab job mapping.
    :return: script commands, or an empty list when the job has no script.
    """
    script = job.get("script") or []
    if isinstance(script, str):
        return [script]
    return [str(line) for line in script]


def _include_key(include: object) -> tuple[str, str, str] | None:
    """Return the immutable identity of one supported GitLab project include.

    :param include: parsed item from the top-level GitLab ``include`` value.
    :return: ``(project, ref, file)`` for a complete project include, otherwise
        ``None``.
    """
    if not isinstance(include, dict):
        return None
    project = include.get("project")
    ref = include.get("ref")
    file = include.get("file")
    if not all(isinstance(value, str) and value for value in (project, ref, file)):
        return None
    return project, ref, file


def _trusted_include_contract(
    registry: dict,
) -> tuple[set[tuple[str, str, str]], dict[str, dict]]:
    """Return closed-world include identities and external template contracts.

    :param registry: parsed gate registry containing ``trusted_includes``.
    :return: immutable include identities plus hidden-template contracts by name.
    """
    identities: set[tuple[str, str, str]] = set()
    templates: dict[str, dict] = {}
    for include in registry.get("trusted_includes") or []:
        identity = _include_key(include)
        if identity is not None:
            identities.add(identity)
        for template in include.get("templates") or []:
            templates[template["name"]] = template
    return identities, templates


def _trusted_external_jobs(registry: dict) -> dict[str, dict]:
    """Return concrete jobs supplied by exact trusted provider includes.

    Concrete provider jobs are not present in the consumer YAML before GitLab
    resolves the include. Their closed-world contract therefore lives beside
    the immutable include identity and is used for required-job and smoke
    coverage without copying the provider job into this repository.

    :param registry: parsed gate registry containing ``trusted_includes``.
    :return: external job contracts keyed by exact job name.
    """
    jobs: dict[str, dict] = {}
    for include in registry.get("trusted_includes") or []:
        for job in include.get("jobs") or []:
            jobs[job["name"]] = job
    return jobs


def _check_trusted_includes(
    ci: dict, registry: dict
) -> tuple[list[str], dict[str, dict]]:
    """Validate exact external includes and return their allowed templates.

    The gate never downloads provider YAML. It trusts only an exact commit and
    closed-world template list declared in the schema-validated local registry;
    GitLab resolves that immutable content before any job can start.

    :param ci: parsed GitLab pipeline.
    :param registry: parsed gate registry.
    :return: validation failures and allowed external template contracts.
    """
    expected, templates = _trusted_include_contract(registry)
    raw_includes = ci.get("include") or []
    includes = raw_includes if isinstance(raw_includes, list) else [raw_includes]
    observed: set[tuple[str, str, str]] = set()
    failures: list[str] = []
    for include in includes:
        identity = _include_key(include)
        if identity is None:
            failures.append(
                "gitlab: every external include must declare project, exact ref, and file"
            )
            continue
        if identity not in expected:
            failures.append(
                "gitlab: include is not an exact registry-trusted provider artifact: "
                f"{identity[0]}@{identity[1]}:{identity[2]}"
            )
            continue
        observed.add(identity)
    for missing in sorted(expected - observed):
        failures.append(
            "gitlab: trusted provider include is declared but not consumed: "
            f"{missing[0]}@{missing[1]}:{missing[2]}"
        )
    return failures, templates


def _external_templates(job: dict) -> list[str]:
    """Return normalized template names from a job's ``extends`` value.

    :param job: parsed GitLab job.
    :return: ordered template names, or an empty list when not extended.
    """
    value = job.get("extends")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return []


def _merge_request_explicitly_blocked(job: dict) -> bool:
    """Report whether the leading rule explicitly denies merge requests.

    GitLab evaluates rules in order. A leading matching rule with ``when:
    never`` makes later protected-branch rules unreachable from a merge request.

    :param job: parsed GitLab job.
    :return: True when merge-request pipelines are explicitly denied.
    """
    rules = job.get("rules") or []
    if not rules or not isinstance(rules[0], dict):
        return False
    return rules[0] == {
        "if": '$CI_PIPELINE_SOURCE == "merge_request_event"',
        "when": "never",
    }


def _protected_template_rules_match(job: dict, protected_when: str) -> bool:
    """Require the exact local rule fence for a provider-owned template.

    :param job: parsed local wrapper job.
    :param protected_when: required protected-ref behavior from the registry.
    :return: True only for project-ci validation selector deny, MR deny,
        protected-ref action, then terminal deny.
    """
    return (job.get("rules") or []) == [
        {
            "if": PROJECT_CI_SELECTOR_DENY,
            "when": "never",
        },
        {
            "if": '$CI_PIPELINE_SOURCE == "merge_request_event"',
            "when": "never",
        },
        {
            "if": (
                '$CI_COMMIT_REF_PROTECTED == "true" && '
                "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"
            ),
            "when": protected_when,
        },
        {"when": "never"},
    ]


def _check_smoke_inventory(registry: dict) -> list[str]:
    """Enforce exact closed-world coverage of the required GitLab jobs.

    :param registry: parsed gate registry containing ``required_jobs``.
    :return: validation failure messages; empty means the inventory is consistent.
    """
    import yaml

    inventory_path = REPO_ROOT / "inventory" / "ci" / "smoke-tests.yaml"
    ci_path = REPO_ROOT / ".gitlab-ci.yml"
    if not inventory_path.is_file():
        return ["CI smoke inventory missing: inventory/ci/smoke-tests.yaml"]
    if not ci_path.is_file():
        return ["CI smoke inventory cannot be checked without .gitlab-ci.yml"]

    try:
        inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8")) or {}
        ci = yaml.safe_load(ci_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return [f"CI smoke inventory input is invalid YAML: {exc}"]

    failures: list[str] = []
    if inventory.get("apiVersion") != "homelab.embedings.ai/v1alpha1":
        failures.append("CI smoke inventory apiVersion mismatch")
    if inventory.get("kind") != "CiSmokeInventory":
        failures.append("CI smoke inventory kind mismatch")

    spec = inventory.get("spec") or {}
    records = spec.get("smokeTests") if isinstance(spec, dict) else None
    if not isinstance(records, list) or not records:
        return failures + ["CI smoke inventory has no spec.smokeTests records"]

    jobs = [record.get("job") for record in records if isinstance(record, dict)]
    if len(jobs) != len(records) or any(not isinstance(job, str) or not job for job in jobs):
        failures.append("every CI smoke record must declare a non-empty job")
        return failures

    duplicates = sorted({job for job in jobs if jobs.count(job) > 1})
    if duplicates:
        failures.append(f"duplicate CI smoke records: {duplicates}")

    required_jobs = set(registry.get("required_jobs") or [])
    smoke_jobs = set(jobs)
    missing = sorted(required_jobs - smoke_jobs)
    extra = sorted(smoke_jobs - required_jobs)
    if missing:
        failures.append(f"required CI jobs missing smoke records: {missing}")
    if extra:
        failures.append(f"smoke records reference non-required CI jobs: {extra}")

    real_jobs = _real_gitlab_jobs(ci)
    external_jobs = _trusted_external_jobs(registry)
    for record in records:
        job = record["job"]
        ci_job = real_jobs.get(job) or external_jobs.get(job)
        if ci_job is None:
            failures.append(f"smoke record references missing GitLab job: {job}")
            continue

        smoke_class = record.get("class")
        if smoke_class not in SMOKE_CLASSES:
            failures.append(f"{job} smoke class is invalid: {smoke_class}")

        command = record.get("command")
        if not isinstance(command, str) or not command:
            failures.append(f"{job} smoke command is missing")
        elif command not in _script_lines(ci_job):
            failures.append(
                f"{job} smoke command is stale or not wired in .gitlab-ci.yml: {command}"
            )

        tools = record.get("requiredTools")
        if (not isinstance(tools, list) or not tools
                or any(not isinstance(tool, str) or not tool for tool in tools)
                or len(tools) != len(set(tools))):
            failures.append(f"{job} requiredTools must be a non-empty unique string list")

        artifact = record.get("artifactUnderTest")
        if (not isinstance(artifact, dict) or not artifact.get("type")
                or not artifact.get("digestSource")):
            failures.append(f"{job} artifactUnderTest needs type and digestSource")

        if record.get("mutation") in (None, ""):
            failures.append(f"{job} mutation classification is missing")
        timeout = record.get("timeoutSeconds")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
            failures.append(f"{job} timeoutSeconds must be an integer from 1 through 3600")
        if record.get("evidencePath") != f"reports/smoke/{job}.json":
            failures.append(f"{job} evidencePath must be reports/smoke/{job}.json")
        if record.get("cleanupPolicy") in (None, "", "none", "not-applicable"):
            failures.append(f"{job} cleanupPolicy is missing")
        if record.get("releaseBlocking") is not True:
            failures.append(f"{job} smoke must be releaseBlocking")

    return failures


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
    runner_tag = registry.get("runner_tag")
    if not isinstance(runner_tag, str) or not runner_tag:
        return ["gate registry runner_tag is missing"], True
    failures, trusted_templates = _check_trusted_includes(ci, registry)
    trusted_external_jobs = _trusted_external_jobs(registry)
    default_tags = (ci.get("default") or {}).get("tags") or []
    real_jobs = _real_gitlab_jobs(ci)
    external_names = [
        job["name"]
        for include in registry.get("trusted_includes") or []
        for job in include.get("jobs") or []
    ]
    duplicate_external_names = sorted(
        {name for name in external_names if external_names.count(name) > 1}
    )
    if duplicate_external_names:
        failures.append(
            f"duplicate trusted external jobs: {duplicate_external_names}"
        )
    for name, contract in trusted_external_jobs.items():
        if name in real_jobs:
            failures.append(
                f"gitlab job {name}: local definition shadows a trusted concrete provider job"
            )
        if contract.get("required") is not True:
            failures.append(f"gitlab job {name}: trusted provider job must be required")
        if contract.get("allowFailure") is not False:
            failures.append(
                f"gitlab job {name}: trusted provider job must set allowFailure:false"
            )
        if contract.get("mutates") is not False:
            failures.append(
                f"gitlab job {name}: mutating concrete provider jobs require "
                "a local protected wrapper"
            )
        if runner_tag not in (contract.get("tags") or []):
            failures.append(
                f"gitlab job {name}: trusted provider job is missing runner tag '{runner_tag}'"
            )
        if not contract.get("stage") or not contract.get("script"):
            failures.append(
                f"gitlab job {name}: trusted provider job lacks analyzable stage or script"
            )
    for name, job in real_jobs.items():
        if job.get("allow_failure") is True:
            failures.append(f"gitlab job {name}: allow_failure:true is forbidden")
        tags = job["tags"] if "tags" in job else default_tags
        if runner_tag not in (tags or []):
            failures.append(f"gitlab job {name}: missing runner tag '{runner_tag}'")
        # Fail closed on a job whose effective body this gate cannot resolve.
        extended = _external_templates(job)
        unknown_templates = sorted(set(extended) - set(trusted_templates))
        if "trigger" in job:
            failures.append(
                f"gitlab job {name}: uses trigger — the meta-gate cannot resolve its effective job")
        elif "extends" in job and not extended:
            failures.append(f"gitlab job {name}: has an invalid extends value")
        elif unknown_templates:
            failures.append(
                f"gitlab job {name}: extends untrusted external templates: {unknown_templates}")
        elif extended:
            if len(extended) != 1:
                failures.append(
                    f"gitlab job {name}: exactly one trusted external template is required")
                continue
            contract = trusted_templates[extended[0]]
            protected_when = "manual" if contract["mutates"] else "on_success"
            if "tags" not in job:
                failures.append(
                    f"gitlab job {name}: trusted external job must declare local runner tags")
            if job.get("allow_failure") is not False:
                failures.append(
                    f"gitlab job {name}: trusted external job must declare allow_failure:false")
            if not _protected_template_rules_match(
                job, protected_when
            ):
                failures.append(
                    f"gitlab job {name}: trusted external job does not preserve "
                    "the registry-declared protected rules")
            if not job.get("stage"):
                failures.append(
                    f"gitlab job {name}: trusted external job must declare a local stage")
        else:
            protected_action = any(
                token in name
                for token in ("apply", "deploy", "publish", "rollback")
            )
            if protected_action and not _merge_request_explicitly_blocked(job):
                failures.append(
                    f"gitlab job {name}: mutation is not explicitly denied "
                    "in a merge-request pipeline")
            if "script" not in job:
                failures.append(
                    f"gitlab job {name}: no script — not analyzable, inline the job body")
    required_jobs = registry.get("required_jobs") or []
    if not required_jobs:
        failures.append(
            "registry declares no required_jobs while .gitlab-ci.yml exists — the required-jobs "
            "check would silently pass")
    known_jobs = set(real_jobs) | set(trusted_external_jobs)
    for required in required_jobs:
        if required not in known_jobs:
            failures.append(f"required GitLab job missing: {required}")
    undeclared_external_jobs = sorted(set(trusted_external_jobs) - set(required_jobs))
    if undeclared_external_jobs:
        failures.append(
            "trusted required provider jobs are absent from required_jobs: "
            f"{undeclared_external_jobs}"
        )
    for diagnostic in registry.get("diagnostic_jobs") or []:
        if diagnostic not in known_jobs:
            failures.append(f"diagnostic GitLab job missing: {diagnostic}")
    return failures, True


def run() -> tuple[bool, list[str], list[str]]:
    """Run every meta-gate check.

    :return: (ok, failures, skipped) — ok is True when there are no failures.
    """
    registry = _load_registry()
    failures = (_check_commands(registry) + _check_mandatory_ids(registry)
                + _check_no_unregistered_scripts(registry)
                + _check_smoke_inventory(registry))
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
