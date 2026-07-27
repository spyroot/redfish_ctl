"""CI enforcement of the gate registry and meta-gate (tools/gate_meta.py, gates/manifest.yaml)."""
import ast
import json
import re
from pathlib import Path

import yaml

from tools import gate_meta

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_FILE = REPO_ROOT / ".gitlab-ci.yml"


def _ci_config() -> dict:
    return yaml.safe_load(CI_FILE.read_text(encoding="utf-8"))


def _gitlab_jobs() -> dict:
    ci = _ci_config()
    return {
        name: job
        for name, job in ci.items()
        if name not in gate_meta.GITLAB_GLOBAL_KEYS
        and isinstance(job, dict)
        and not name.startswith(".")
    }


def _script_lines(job: dict) -> list[str]:
    script = job.get("script") or []
    if isinstance(script, str):
        return [script]
    return [str(line) for line in script]


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


def test_gitlab_declares_exactly_one_focused_gate_job() -> None:
    """Only one job may consume FOCUSED_GATE and it must go through check.sh."""
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


def test_internal_api_web_focused_dispatch_selects_only_the_focused_job() -> None:
    """Internal API/web dispatch with FOCUSED_GATE creates diagnostic evidence only."""
    for source in ("api", "web"):
        selected = _selected_jobs(
            CI_PIPELINE_SOURCE=source,
            FOCUSED_GATE="unit.all",
            MERGE_PROFILE=None,
        )
        assert selected == ["focused-gate"]


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
    forbidden = {"gate-integration", "k8s-live-check", "deploy-apply", "publish-github"}
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
        assert "deploy-apply" not in selected
        assert "publish-github" not in selected
