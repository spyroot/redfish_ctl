#!/usr/bin/env python3
"""Validate local contracts against exact Standards and Builder revisions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
EXACT_SHA = re.compile(r"^[0-9a-f]{40}$")
IMMUTABLE_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
APPROVED_GITLAB_PREFIX = "https://gitlab.rnd.embedings.ai/"


class SchemaGateError(ValueError):
    """Raised when an exact contract cannot be fetched or validated."""


def _load_yaml(path: Path) -> dict:
    """Load one YAML mapping from disk."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SchemaGateError(f"{path.name} must contain a mapping")
    return value


def _fetch_exact(repository: str, revision: str) -> tempfile.TemporaryDirectory:
    """Fetch one exact protected source tree without persisting credentials."""
    if not repository.startswith(APPROVED_GITLAB_PREFIX):
        raise SchemaGateError("contract repository is outside approved Internal GitLab")
    if not EXACT_SHA.fullmatch(revision):
        raise SchemaGateError("contract revision is not an exact commit")
    workspace = tempfile.TemporaryDirectory(prefix="redfish_ctl-schema-")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    job_token = env.get("CI_JOB_TOKEN", "").strip()
    if job_token:
        env.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": f"JOB-TOKEN: {job_token}",
            }
        )
    commands = [
        ["git", "init", "--quiet", workspace.name],
        ["git", "-C", workspace.name, "remote", "add", "origin", repository],
        [
            "git",
            "-c",
            "credential.helper=",
            "-C",
            workspace.name,
            "fetch",
            "--quiet",
            "--depth=1",
            "origin",
            revision,
        ],
        ["git", "-C", workspace.name, "checkout", "--quiet", "--detach", "FETCH_HEAD"],
    ]
    try:
        for command in commands:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                env=env,
            )
            if proc.returncode != 0:
                raise SchemaGateError(
                    f"exact contract fetch failed at {command[1]} (exit {proc.returncode})"
                )
        head = subprocess.run(
            ["git", "-C", workspace.name, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=env,
        )
        if head.returncode != 0 or head.stdout.strip() != revision:
            raise SchemaGateError("fetched contract identity does not match its binding")
    except Exception:
        workspace.cleanup()
        raise
    return workspace


def _validate(document: dict, schema_path: Path, label: str) -> None:
    """Validate one document and render a bounded diagnostic on failure."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    try:
        jsonschema.validate(document, schema)
    except jsonschema.ValidationError as exc:
        raise SchemaGateError(f"{label} invalid: {exc.message}") from exc


def _validate_provider_include(provider_root: Path, provider_binding: dict) -> None:
    """Verify all trusted includes and the imported CI evidence job agree."""
    ci = _load_yaml(REPO_ROOT / ".gitlab-ci.yml")
    manifest = _load_yaml(REPO_ROOT / "gates/manifest.yaml")
    revision = provider_binding["spec"]["source"]["revision"]
    expected_includes = [
        {
            "project": record["project"],
            "ref": record["ref"],
            "file": record["file"],
        }
        for record in manifest.get("trusted_includes", [])
    ]
    observed_includes = ci.get("include") or []
    if isinstance(observed_includes, dict):
        observed_includes = [observed_includes]
    observed_identities = {
        (record.get("project"), record.get("ref"), record.get("file"))
        for record in observed_includes
        if isinstance(record, dict)
    }
    expected_identities = {
        (record["project"], record["ref"], record["file"])
        for record in expected_includes
    }
    if (
        not isinstance(observed_includes, list)
        or len(observed_includes) != len(expected_includes)
        or observed_identities != expected_identities
    ):
        raise SchemaGateError(
            ".gitlab-ci.yml includes do not match gates/manifest.yaml trusted_includes"
        )
    resource_include = {
        "project": provider_binding["spec"]["dispatch"]["project"],
        "ref": revision,
        "file": "/ci/templates/project-ci-resource-jobs.yml",
    }
    if resource_include not in observed_includes:
        raise SchemaGateError(".gitlab-ci.yml does not pin the bound Builder CI include")
    default_image = str((ci.get("default") or {}).get("image", ""))
    if not IMMUTABLE_IMAGE.fullmatch(default_image):
        raise SchemaGateError(".gitlab-ci.yml default image is not digest-pinned")
    project_command = str((ci.get("variables") or {}).get("PROJECT_CI_CPU_COMMAND", ""))
    if project_command != "./scripts/project_ci_entrypoint.sh":
        raise SchemaGateError("PROJECT_CI_CPU_COMMAND does not select the project CI adapter")
    project_entrypoint = REPO_ROOT / project_command.removeprefix("./")
    if not project_entrypoint.is_file() or not (project_entrypoint.stat().st_mode & 0o111):
        raise SchemaGateError("project CI adapter is missing or not executable")

    include = _load_yaml(provider_root / "ci/templates/project-ci-resource-jobs.yml")
    imported = include.get("project-ci-cpu-validation")
    if not isinstance(imported, dict):
        raise SchemaGateError("bound Builder include lacks project-ci-cpu-validation")
    if imported.get("allow_failure") is not False:
        raise SchemaGateError("Builder project-ci-cpu-validation is not required")
    if not IMMUTABLE_IMAGE.fullmatch(str(imported.get("image", ""))):
        raise SchemaGateError("Builder project-ci-cpu-validation image is not immutable")
    tags = imported.get("tags") or []
    if "homelab-k8s" not in tags or "homelab-k8s-validation" not in tags:
        raise SchemaGateError("Builder project-ci-cpu-validation runner tags are incomplete")
    rules_text = repr(imported.get("rules") or [])
    required_selectors = (
        '$FOCUSED_GATE != null && $FOCUSED_GATE != ""',
        '$PROJECT_CI_PROFILE == "focused"',
        '$PROJECT_CI_PROFILE == "full"',
    )
    if any(selector not in rules_text for selector in required_selectors):
        raise SchemaGateError("Builder project-ci-cpu-validation selectors are incomplete")

    smoke = _load_yaml(REPO_ROOT / "inventory/ci/smoke-tests.yaml")
    records = [
        record
        for record in smoke.get("spec", {}).get("smokeTests", [])
        if record.get("job") == "project-ci-cpu-validation"
    ]
    if len(records) != 1 or records[0].get("command") not in (imported.get("script") or []):
        raise SchemaGateError("Builder imported job and smoke command do not match")


def run() -> None:
    """Execute the exact-schema validation contract."""
    binding = _load_yaml(REPO_ROOT / "standards-binding.yaml")
    standards_source = binding.get("spec", {}).get("source", {})
    standards_workspace = _fetch_exact(
        str(standards_source.get("repository", "")),
        str(standards_source.get("revision", "")),
    )
    try:
        standards_root = Path(standards_workspace.name)
        _validate(
            binding,
            standards_root / "schemas/project-standards-binding.schema.json",
            "standards-binding.yaml",
        )
        declarations = binding["spec"]["providers"]
        for declaration in declarations:
            provider_path = REPO_ROOT / declaration["binding"]
            provider = _load_yaml(provider_path)
            _validate(
                provider,
                standards_root / "schemas/project-provider-binding.schema.json",
                provider_path.name,
            )
            if provider["metadata"]["name"] != declaration["name"]:
                raise SchemaGateError(
                    f"{provider_path.name} metadata.name disagrees with standards-binding.yaml"
                )
            provider_source = provider["spec"]["source"]
            provider_workspace = _fetch_exact(
                provider_source["repository"], provider_source["revision"]
            )
            try:
                provider_root = Path(provider_workspace.name)
                if not (provider_root / provider["spec"]["contract"]["path"]).is_file():
                    raise SchemaGateError("bound provider contract path is missing")
                _validate_provider_include(provider_root, provider)
            finally:
                provider_workspace.cleanup()
    finally:
        standards_workspace.cleanup()

    _validate(
        _load_yaml(REPO_ROOT / "gates/manifest.yaml"),
        REPO_ROOT / "schemas/gates.schema.json",
        "gates/manifest.yaml",
    )
    for schema_path in sorted((REPO_ROOT / "schemas").glob("*-evidence.schema.json")):
        jsonschema.Draft202012Validator.check_schema(
            json.loads(schema_path.read_text(encoding="utf-8"))
        )


def main() -> int:
    """CLI adapter for the repository schema gate."""
    try:
        run()
    except (OSError, SchemaGateError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"repo.schemas: {exc}", file=sys.stderr)
        return 1
    print("repo.schemas: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
