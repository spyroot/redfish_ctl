"""CI enforcement of the gate registry and meta-gate (tools/gate_meta.py, gates/manifest.yaml)."""
import ast
import json
import re
import textwrap
from pathlib import Path

import yaml

from tools import gate_meta

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_FILE = REPO_ROOT / ".gitlab-ci.yml"
BUILDER_PROJECT_INCLUDE_FILE = "/ci/templates/project-service.yml"
BUILDER_RESOURCE_INCLUDE_FILE = "/ci/templates/project-ci-resource-jobs.yml"
PROJECT_SERVICE_JOBS = {
    "project-service-image-publish": ".builder-project-image-publish",
    "project-service-chart-publish": ".builder-project-chart-publish",
    "project-service-deploy-plan": ".builder-project-deploy-plan",
    "project-service-deploy": ".builder-project-deploy",
    "project-service-verify": ".builder-project-verify",
    "project-service-live-test": ".builder-project-live-test",
    "project-service-rollback": ".builder-project-rollback",
    "project-service-release-evidence": ".builder-project-release-evidence",
}
PROJECT_SERVICE_TEMPLATE_NAMES = set(PROJECT_SERVICE_JOBS.values())
MUTATING_PROJECT_SERVICE_TEMPLATES = {
    ".builder-project-image-publish",
    ".builder-project-chart-publish",
    ".builder-project-deploy",
    ".builder-project-rollback",
}


def _ci_config() -> dict:
    return yaml.safe_load(CI_FILE.read_text(encoding="utf-8"))


def _gitlab_jobs() -> dict:
    """Return local jobs; imported Builder jobs have separate exact-pin tests."""
    ci = _ci_config()
    return {
        name: job
        for name, job in ci.items()
        if name not in gate_meta.GITLAB_GLOBAL_KEYS
        and isinstance(job, dict)
        and not name.startswith(".")
        and name not in gate_meta.BUILDER_IMPORTED_JOBS
    }


def _script_lines(job: dict) -> list[str]:
    script = job.get("script") or []
    if isinstance(script, str):
        return [script]
    return [str(line) for line in script]


def _trusted_project_service_registry(
    *,
    required_jobs: list[str] | None = None,
) -> dict:
    """Return the minimal registry contract for one trusted Builder include.

    :param required_jobs: GitLab job names the meta-gate must find.
    :return: a registry dict suitable for direct ``gate_meta`` unit checks.
    """
    return {
        "version": 1,
        "runner_tag": "homelab-k8s",
        "required_jobs": required_jobs or ["gate-merge"],
        "mandatory_ids": [],
        "trusted_includes": [
            {
                "project": "spyroot/builder",
                "ref": "a" * 40,
                "file": BUILDER_PROJECT_INCLUDE_FILE,
                "templates": [
                    {
                        "name": name,
                        "mutates": name in MUTATING_PROJECT_SERVICE_TEMPLATES,
                    }
                    for name in sorted(PROJECT_SERVICE_TEMPLATE_NAMES)
                ],
            }
        ],
        "gates": [],
    }


def _check_temp_gitlab(
    tmp_path: Path,
    monkeypatch,
    body: str,
    registry: dict,
) -> tuple[list[str], bool]:
    """Run the GitLab meta-check against a temporary pipeline file."""
    (tmp_path / ".gitlab-ci.yml").write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setattr(gate_meta, "REPO_ROOT", tmp_path)
    return gate_meta._check_gitlab(registry)


def _registry_trusted_include() -> dict:
    """Return the trusted project-service include from the live registry."""
    registry = gate_meta._load_registry()
    records = registry.get("trusted_includes") or []
    matches = [
        record for record in records
        if record.get("file") == BUILDER_PROJECT_INCLUDE_FILE
    ]
    assert len(matches) == 1, "the project-service include must be single-sourced"
    include = matches[0]
    assert include["project"] == "spyroot/builder"
    assert include["file"] == BUILDER_PROJECT_INCLUDE_FILE
    assert re.fullmatch(r"[0-9a-f]{40}", include["ref"])
    contracts = {item["name"]: item["mutates"] for item in include["templates"]}
    assert set(contracts) == PROJECT_SERVICE_TEMPLATE_NAMES
    assert {
        name for name, mutates in contracts.items() if mutates
    } == MUTATING_PROJECT_SERVICE_TEMPLATES
    return include


_ALLOWED_GITLAB_EXPR_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.Compare,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Eq,
    ast.NotEq,
)


def _eval_gitlab_if(expression: str, variables: dict[str, str | None]) -> bool:
    """Evaluate the small GitLab ``rules:if`` subset used in this pipeline.

    Supports variable equality/inequality, ``null``, ``&&``, ``||`` and
    parentheses. This is intentionally narrow so a rule shape outside the local
    contract fails the test instead of being guessed.

    :param expression: GitLab ``rules:if`` expression.
    :param variables: simulated GitLab CI variables.
    :return: whether the expression matches.
    """
    prepared = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", r'value("\1")', expression)
    prepared = prepared.replace("&&", " and ").replace("||", " or ")
    prepared = re.sub(r"\bnull\b", "None", prepared)
    tree = ast.parse(prepared, mode="eval")
    for node in ast.walk(tree):
        assert isinstance(node, _ALLOWED_GITLAB_EXPR_NODES), (
            f"unsupported GitLab rule expression node {type(node).__name__}: {expression}"
        )
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name) and node.func.id == "value", expression
            assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant), expression

    def evaluate(node: ast.AST):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.BoolOp):
            values = [bool(evaluate(value)) for value in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
        if isinstance(node, ast.Compare):
            left = evaluate(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = evaluate(comparator)
                if isinstance(op, ast.Eq):
                    ok = left == right
                elif isinstance(op, ast.NotEq):
                    ok = left != right
                else:
                    raise AssertionError(f"unsupported comparison in rule: {expression}")
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            return variables.get(str(node.args[0].value))
        if isinstance(node, ast.Constant):
            return node.value
        raise AssertionError(f"unsupported GitLab rule expression: {expression}")

    return bool(evaluate(tree))


def _job_selected(job: dict, variables: dict[str, str | None]) -> bool:
    rules = job.get("rules")
    if not rules:
        return True
    for rule in rules:
        if not isinstance(rule, dict):
            raise AssertionError(f"unsupported rule shape: {rule!r}")
        expression = rule.get("if")
        if expression is None or _eval_gitlab_if(expression, variables):
            return rule.get("when") != "never"
    return False


def _selected_jobs(**overrides: str | None) -> list[str]:
    variables: dict[str, str | None] = {
        "CI_SERVER_HOST": "gitlab.rnd.embedings.ai",
        "CI_PIPELINE_SOURCE": "push",
        "CI_COMMIT_BRANCH": "feature/focused-ci",
        "CI_DEFAULT_BRANCH": "main",
        "CI_COMMIT_REF_PROTECTED": "false",
        "FOCUSED_GATE": None,
        "MERGE_PROFILE": None,
    }
    variables.update(overrides)
    return [name for name, job in _gitlab_jobs().items() if _job_selected(job, variables)]


def _profile_enum(node):
    """Find the gate-profile enum in the JSON schema, wherever it is nested.

    The schema's shape is not this test's business — only that a single string enum constrains a
    gate's profile. Searching for it keeps the allowed set in one place instead of copying it here.

    :param node: any node of the parsed JSON schema.
    :return: the list of allowed profile names, or an empty list when no such enum exists.
    """
    if isinstance(node, dict):
        if node.get("type") == "string" and isinstance(node.get("enum"), list) and "merge" in node["enum"]:
            return node["enum"]
        for value in node.values():
            found = _profile_enum(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _profile_enum(value)
            if found:
                return found
    return []


def test_meta_gate_passes():
    """gates/manifest.yaml and the pipeline are internally consistent (the meta-gate is green).

    This is the ``repo.meta`` gate run inside the offline suite, so a registry
    that references a missing/non-executable command, omits a mandatory ID, or
    (once they exist) mis-wires a GitLab job or a module, fails the build here.
    """
    ok, failures, _skipped = gate_meta.run()
    assert ok, "meta-gate failures:\n" + "\n".join(failures)


def test_registry_lists_every_mandatory_id_with_a_command():
    """Each mandatory gate ID is registered and carries an executable-looking command."""
    registry = gate_meta._load_registry()
    by_id = {g.get("id"): g for g in registry["gates"]}
    for mandatory in registry.get("mandatory_ids", []):
        assert mandatory in by_id, f"mandatory id {mandatory} is not registered"
        assert by_id[mandatory].get("command"), f"{mandatory} has no command"


def test_every_gate_declares_profile_and_mutates():
    """Every gate carries a schema-valid profile and an explicit mutation classification.

    The allowed set is read from schemas/gates.schema.json rather than repeated here. Hardcoding it
    made this test a fourth copy of the profile list — alongside check.sh, run.sh and the schema — and
    adding the repository-export profile broke it while the other three were already updated.
    """
    schema = json.loads((REPO_ROOT / "schemas" / "gates.schema.json").read_text(encoding="utf-8"))
    allowed = set(_profile_enum(schema))
    assert allowed, "the schema declares no profile enum; nothing constrains a gate's profile"

    registry = gate_meta._load_registry()
    for gate in registry["gates"]:
        assert gate.get("profile") in allowed, gate
        assert isinstance(gate.get("mutates"), bool), f"{gate.get('id')} lacks a bool 'mutates'"


def test_registry_declares_exactly_one_diagnostic_focused_gate_job() -> None:
    """The focused CI job is diagnostic-only and cannot replace required merge evidence."""
    registry = gate_meta._load_registry()
    assert registry.get("diagnostic_jobs") == ["focused-gate"]
    assert "focused-gate" not in registry.get("required_jobs", [])


def test_registry_pins_exact_builder_project_service_include() -> None:
    """The trusted project-service include is a registry-declared exact commit."""
    _registry_trusted_include()


def test_registry_pins_builder_resource_include_to_provider_binding() -> None:
    """The imported CI job uses the exact tracked Builder provider revision."""
    registry = gate_meta._load_registry()
    records = registry.get("trusted_includes") or []
    matches = [
        record for record in records
        if record.get("file") == BUILDER_RESOURCE_INCLUDE_FILE
    ]
    assert len(matches) == 1, "the CI resource include must be single-sourced"
    binding = yaml.safe_load(
        (REPO_ROOT / "builder-binding.yaml").read_text(encoding="utf-8")
    )
    assert matches[0]["project"] == "spyroot/builder"
    assert matches[0]["ref"] == binding["spec"]["source"]["revision"]
    assert matches[0]["templates"] == []


def test_meta_gate_accepts_registry_trusted_include_and_allowed_template(
    tmp_path,
    monkeypatch,
) -> None:
    """A trusted exact include plus a locally guarded allowed template is analyzable."""
    registry = _trusted_project_service_registry(
        required_jobs=["gate-merge", "project-service-deploy-plan"]
    )
    ref = registry["trusted_includes"][0]["ref"]

    failures, ran = _check_temp_gitlab(
        tmp_path,
        monkeypatch,
        f"""
        include:
          - project: spyroot/builder
            ref: {ref}
            file: {BUILDER_PROJECT_INCLUDE_FILE}

        gate-merge:
          stage: validate
          tags: [homelab-k8s]
          script: [./scripts/check.sh --profile merge]
          artifacts:
            paths:
              - reports/ci/gate-merge.json
              - reports/smoke/gate-merge.json

        project-service-deploy-plan:
          stage: validate
          tags: [homelab-k8s]
          allow_failure: false
          artifacts:
            paths:
              - reports/ci/project-service-deploy-plan.json
              - reports/smoke/project-service-deploy-plan.json
          rules:
            - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
              when: never
            - if: '$CI_COMMIT_REF_PROTECTED == "true" && $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
              when: on_success
            - when: never
          extends: .builder-project-deploy-plan
        """,
        registry,
    )

    assert ran
    assert failures == []


def test_meta_gate_rejects_floating_or_untrusted_provider_include() -> None:
    """Floating refs, unknown providers, unknown files, and local includes fail closed."""
    registry = _trusted_project_service_registry()
    ref = registry["trusted_includes"][0]["ref"]
    cases = (
        {"project": "spyroot/builder", "ref": "main", "file": BUILDER_PROJECT_INCLUDE_FILE},
        {"project": "spyroot/other", "ref": ref, "file": BUILDER_PROJECT_INCLUDE_FILE},
        {"project": "spyroot/builder", "ref": ref, "file": "/ci/templates/other.yml"},
        {"local": "ci/project-service.yml"},
    )

    for include in cases:
        failures, allowed_templates = gate_meta._check_trusted_includes(
            {"include": [include]}, registry
        )
        assert failures, include
        assert set(allowed_templates) == PROJECT_SERVICE_TEMPLATE_NAMES


def test_meta_gate_rejects_unknown_external_template(
    tmp_path,
    monkeypatch,
) -> None:
    """A local job may extend only templates declared by the trusted include contract."""
    registry = _trusted_project_service_registry()
    ref = registry["trusted_includes"][0]["ref"]

    failures, ran = _check_temp_gitlab(
        tmp_path,
        monkeypatch,
        f"""
        include:
          - project: spyroot/builder
            ref: {ref}
            file: {BUILDER_PROJECT_INCLUDE_FILE}

        gate-merge:
          stage: validate
          tags: [homelab-k8s]
          allow_failure: false
          rules:
            - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
          extends: .builder-project-unknown
        """,
        registry,
    )

    assert ran
    assert any("untrusted external templates" in failure for failure in failures)


def test_meta_gate_rejects_unconditional_project_service_deploy(
    tmp_path,
    monkeypatch,
) -> None:
    """An unconditional provider deploy override cannot bypass the MR fence."""
    registry = _trusted_project_service_registry(
        required_jobs=["project-service-deploy"]
    )
    ref = registry["trusted_includes"][0]["ref"]

    failures, ran = _check_temp_gitlab(
        tmp_path,
        monkeypatch,
        f"""
        include:
          - project: spyroot/builder
            ref: {ref}
            file: {BUILDER_PROJECT_INCLUDE_FILE}

        project-service-deploy:
          stage: deploy
          tags: [homelab-k8s]
          allow_failure: false
          rules:
            - when: manual
          extends: .builder-project-deploy
        """,
        registry,
    )

    assert ran
    assert any("does not preserve" in failure for failure in failures)


def test_meta_gate_rejects_early_rules_for_every_provider_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    """Publication, deployment, and rollback require a leading MR deny."""
    registry = _trusted_project_service_registry()
    ref = registry["trusted_includes"][0]["ref"]

    for index, template in enumerate(sorted(MUTATING_PROJECT_SERVICE_TEMPLATES)):
        job_name = f"unsafe-mutation-{index}"
        failures, ran = _check_temp_gitlab(
            tmp_path,
            monkeypatch,
            f"""
            include:
              - project: spyroot/builder
                ref: {ref}
                file: {BUILDER_PROJECT_INCLUDE_FILE}

            gate-merge:
              stage: validate
              tags: [homelab-k8s]
              script: [./scripts/check.sh --profile merge]

            {job_name}:
              stage: deploy
              tags: [homelab-k8s]
              allow_failure: false
              rules:
                - if: '$CI_COMMIT_REF_PROTECTED == "true"'
                  when: manual
                - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
                  when: never
                - when: never
              extends: {template}
            """,
            registry,
        )

        assert ran
        assert any("does not preserve" in failure for failure in failures), template


def test_gitlab_consumes_builder_project_service_contract_without_local_secrets() -> None:
    """The real pipeline wires the exact include and local project-service wrappers."""
    trusted_include = _registry_trusted_include()
    ci = _ci_config()
    raw_includes = ci.get("include") or []
    includes = raw_includes if isinstance(raw_includes, list) else [raw_includes]
    include_identities = {
        gate_meta._include_key(include)
        for include in includes
        if gate_meta._include_key(include) is not None
    }

    assert (
        trusted_include["project"],
        trusted_include["ref"],
        trusted_include["file"],
    ) in include_identities

    jobs = _gitlab_jobs()
    missing_jobs = sorted(set(PROJECT_SERVICE_JOBS) - set(jobs))
    assert missing_jobs == []

    for name in sorted(PROJECT_SERVICE_JOBS):
        job = jobs[name]
        extended = gate_meta._external_templates(job)
        job_text = repr(job)
        assert extended, f"{name} must inherit a trusted provider template"
        assert extended == [PROJECT_SERVICE_JOBS[name]]
        trusted_names = {item["name"] for item in trusted_include["templates"]}
        assert set(extended) <= trusted_names
        mutates = PROJECT_SERVICE_JOBS[name] in MUTATING_PROJECT_SERVICE_TEMPLATES
        expected_when = "manual" if mutates else "on_success"
        assert gate_meta._protected_template_rules_match(job, expected_when)
        assert job.get("stage") in {"validate", "integration", "deploy", "publish"}
        assert "homelab-k8s" in job.get("tags", [])
        assert job.get("allow_failure") is False
        assert job.get("rules"), f"{name} must declare local rules"
        assert "script" not in job, f"{name} must not shadow the provider template body"
        assert "trigger" not in job, f"{name} must not hide work in a child pipeline"
        assert "/Users/" not in job_text
        assert "~/" not in job_text
        assert "PRIVATE-TOKEN" not in job_text
        assert "Authorization:" not in job_text

    image_publish_setup = "\n".join(
        str(line)
        for line in jobs["project-service-image-publish"].get("before_script", [])
    )
    assert "git lfs install --local" in image_publish_setup
    assert "DSP2043_2026.1.zip" in image_publish_setup


def test_gitlab_uses_full_history_checkout_for_exact_ref_dispatches() -> None:
    """Direct exact-ref pipelines need origin/main history for repo.format merge-base."""
    variables = _ci_config().get("variables") or {}
    assert variables.get("GIT_DEPTH") == "0"


def test_gitlab_declares_exactly_one_focused_gate_job() -> None:
    """Only the local job consumes FOCUSED_GATE; the imported overlay excludes it."""
    jobs = _gitlab_jobs()
    focused_jobs = [
        name
        for name, job in jobs.items()
        if any("--gate" in line and "FOCUSED_GATE" in line for line in _script_lines(job))
    ]
    assert focused_jobs == ["focused-gate"]

    focused = jobs["focused-gate"]
    rules_text = repr(focused.get("rules"))
    script = "\n".join(_script_lines(focused))
    assert focused.get("stage") == "validate"
    assert "homelab-k8s" in focused.get("tags", [])
    assert '$CI_SERVER_HOST == "gitlab.rnd.embedings.ai"' in rules_text
    assert '$CI_PIPELINE_SOURCE == "web"' in rules_text
    assert '$CI_PIPELINE_SOURCE == "api"' in rules_text
    assert "$FOCUSED_GATE != null" in rules_text
    assert '$FOCUSED_GATE != ""' in rules_text
    assert './scripts/check.sh --profile merge --gate "${FOCUSED_GATE:-unit.all}"' in script
    assert "scripts/gates/run.sh" not in script

    provider_rules = repr(jobs["project-ci-cpu-validation"].get("rules"))
    assert "FOCUSED_GATE" not in provider_rules
    assert '$PROJECT_CI_PROFILE == "focused"' in provider_rules
    assert '$PROJECT_CI_PROFILE == "full"' in provider_rules


def test_internal_api_web_focused_dispatch_selects_only_local_focused_job() -> None:
    """FOCUSED_GATE selects one local job after the imported-rule override."""
    for source in ("api", "web"):
        selected = _selected_jobs(
            CI_PIPELINE_SOURCE=source,
            FOCUSED_GATE="unit.all",
            MERGE_PROFILE=None,
        )
        assert selected == ["focused-gate"]


def test_builder_focused_profile_selects_only_provider_cpu_job() -> None:
    """Builder's focused profile selects one provider job with the unit default."""
    for source in ("api", "web"):
        selected = _selected_jobs(
            CI_PIPELINE_SOURCE=source,
            FOCUSED_GATE=None,
            MERGE_PROFILE=None,
            PROJECT_CI_PROFILE="focused",
        )
        assert selected == ["project-ci-cpu-validation"]


def test_internal_api_web_merge_dispatch_selects_only_gate_merge() -> None:
    """MERGE_PROFILE=merge without FOCUSED_GATE creates full merge evidence only."""
    for source in ("api", "web"):
        selected = _selected_jobs(
            CI_PIPELINE_SOURCE=source,
            FOCUSED_GATE=None,
            MERGE_PROFILE="merge",
        )
        assert selected == ["gate-merge"]


def test_focused_and_merge_dispatch_exclude_private_follow_on_jobs() -> None:
    """API/web dispatch cannot also enqueue integration, deploy, or publish jobs."""
    forbidden = {
        "gate-integration",
        "k8s-live-check",
        "publish-github",
        *PROJECT_SERVICE_JOBS,
    }
    focused = set(_selected_jobs(
        CI_PIPELINE_SOURCE="api",
        FOCUSED_GATE="unit.all",
        MERGE_PROFILE=None,
    ))
    merge = set(_selected_jobs(
        CI_PIPELINE_SOURCE="web",
        FOCUSED_GATE=None,
        MERGE_PROFILE="merge",
    ))
    assert focused.isdisjoint(forbidden), focused
    assert merge.isdisjoint(forbidden), merge


def test_other_gitlab_hosts_open_no_private_dispatch_route() -> None:
    """Only the authoritative Internal GitLab host may use private dispatch."""
    for host in ("gitlab.com", "gitlab.example.net"):
        selected = _selected_jobs(
            CI_SERVER_HOST=host,
            CI_PIPELINE_SOURCE="api",
            FOCUSED_GATE="unit.all",
            MERGE_PROFILE="merge",
        )
        assert "focused-gate" not in selected
        assert "gate-merge" not in selected
        assert set(selected).isdisjoint(PROJECT_SERVICE_JOBS)
        assert "publish-github" not in selected
