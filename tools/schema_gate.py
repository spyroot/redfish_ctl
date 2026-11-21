#!/usr/bin/env python3
"""Validate local contracts against exact Standards and declared provider revisions."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from urllib.parse import urlsplit

import jsonschema
import yaml

try:
    from tools.provider_contract import (
        ProviderContractError,
        normalized_base_url,
        provider_base_url,
        require_provider_repository,
    )
except ModuleNotFoundError:  # Direct ``python tools/schema_gate.py`` execution.
    from provider_contract import (  # type: ignore[no-redef]
        ProviderContractError,
        normalized_base_url,
        provider_base_url,
        require_provider_repository,
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
EXACT_SHA = re.compile(r"^[0-9a-f]{40}$")
IMMUTABLE_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
STANDARDS_PROOF_PATHS = ("manifest.yaml", "schemas/project-standards-binding.schema.json")
MAX_STANDARDS_PROOF_BYTES = 64 * 1024


class SchemaGateError(ValueError):
    """Raised when an exact contract cannot be checked out or validated."""


def _load_yaml(path: Path) -> dict:
    """Load one YAML mapping from disk."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SchemaGateError(f"{path.name} must contain a mapping")
    return value


def _runtime_ci_base_url(env: dict[str, str]) -> str | None:
    """Resolve the runtime origin for an authenticated GitLab fetch.

    :param env: CI environment with the job token and server URL.
    :returns: Normalized server URL, or None when no job token is present.
    """
    if not env.get("CI_JOB_TOKEN", "").strip():
        return None
    runtime_value = env.get("CI_SERVER_URL", "").strip()
    if not runtime_value:
        raise SchemaGateError("CI_SERVER_URL is required when CI_JOB_TOKEN is present")
    return normalized_base_url(runtime_value, label="CI_SERVER_URL")


def _runtime_provider_base_url(root: Path, env: dict[str, str]) -> str:
    """Bind CI job-token use to the provider host declared by the project.

    :param root: Consumer repository root.
    :param env: CI environment used for the fetch.
    :returns: Normalized provider base URL.
    """
    configured = provider_base_url(root)
    runtime = _runtime_ci_base_url(env)
    if runtime is not None and runtime != configured:
        raise SchemaGateError("CI_SERVER_URL disagrees with the bound provider route")
    return configured


def _standards_base_url(source: dict, env: dict[str, str]) -> str:
    """Resolve the exact Standards fetch boundary from its own source.

    :param source: Standards source from the project binding.
    :param env: CI environment used for the fetch.
    :returns: Allowed origin for the Standards repository.
    """
    runtime = _runtime_ci_base_url(env)
    if runtime is not None:
        return runtime
    repository = normalized_base_url(
        source.get("repository", ""), label="Standards repository"
    )
    parsed = urlsplit(repository)
    return f"{parsed.scheme}://{parsed.netloc}"


def _fetch_exact(
    repository: str,
    revision: str,
    *,
    approved_base_url: str,
    env: dict[str, str] | None = None,
) -> tempfile.TemporaryDirectory:
    """Fetch one exact protected source tree without persisting credentials."""
    try:
        repository = require_provider_repository(repository, approved_base_url)
    except ProviderContractError as exc:
        raise SchemaGateError(str(exc)) from exc
    if not repository.endswith(".git"):
        repository = f"{repository}.git"
    if not EXACT_SHA.fullmatch(revision):
        raise SchemaGateError("contract revision is not an exact commit")
    workspace = tempfile.TemporaryDirectory(prefix="redfish_ctl-schema-")
    selected_env = os.environ if env is None else env
    fetch_env = {**selected_env, "GIT_TERMINAL_PROMPT": "0"}
    job_token = fetch_env.get("CI_JOB_TOKEN", "").strip()
    if job_token:
        basic_credential = base64.b64encode(
            f"gitlab-ci-token:{job_token}".encode("utf-8")
        ).decode("ascii")
        fetch_env.update(
            {
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": f"http.{repository}.extraHeader",
                "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic_credential}",
                "GIT_CONFIG_KEY_1": "http.followRedirects",
                "GIT_CONFIG_VALUE_1": "false",
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
                env=fetch_env,
            )
            if proc.returncode != 0:
                repository_name = repository.rstrip("/").rsplit("/", 1)[-1]
                raise SchemaGateError(
                    f"exact contract fetch failed for {repository_name} "
                    f"at {command[0]} (exit {proc.returncode})"
                )
        head = subprocess.run(
            ["git", "-C", workspace.name, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=fetch_env,
        )
        if head.returncode != 0 or head.stdout.strip() != revision:
            raise SchemaGateError("fetched contract identity does not match its binding")
    except Exception:
        workspace.cleanup()
        raise
    return workspace


def _checkout_exact_local(
    local_path: str,
    revision: str,
) -> tempfile.TemporaryDirectory:
    """Clone one local authority at its binding's exact revision."""
    if not EXACT_SHA.fullmatch(revision):
        raise SchemaGateError("contract revision is not an exact commit")
    if not local_path.strip():
        raise SchemaGateError("binding has no local authority checkout")
    try:
        authority = Path(local_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SchemaGateError("bound local authority checkout is unavailable") from exc
    if not authority.is_dir():
        raise SchemaGateError("bound local authority checkout is not a directory")
    workspace = tempfile.TemporaryDirectory(prefix="redfish_ctl-schema-")
    commands = [
        [
            "git",
            "clone",
            "--quiet",
            "--shared",
            "--no-checkout",
            "--",
            str(authority),
            workspace.name,
        ],
        ["git", "-C", workspace.name, "checkout", "--quiet", "--detach", revision],
    ]
    try:
        for command in commands:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if proc.returncode != 0:
                raise SchemaGateError(
                    "exact local authority checkout failed "
                    f"at {command[0]} (exit {proc.returncode})"
                )
        head = subprocess.run(
            ["git", "-C", workspace.name, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if head.returncode != 0 or head.stdout.strip() != revision:
            raise SchemaGateError("local authority identity does not match its binding")
    except Exception:
        workspace.cleanup()
        raise
    return workspace


def _checkout_exact_authority(
    source: dict,
    *,
    approved_base_url: str,
    env: dict[str, str],
) -> tempfile.TemporaryDirectory:
    """Use a local checkout when available, otherwise fetch the exact repository."""
    local_path = str(source.get("localPath", "")).strip()
    revision = str(source.get("revision", ""))
    if local_path and Path(local_path).expanduser().is_dir():
        return _checkout_exact_local(local_path, revision)
    return _fetch_exact(
        str(source.get("repository", "")),
        revision,
        approved_base_url=approved_base_url,
        env=env,
    )


def _load_local_standards_proof(
    proof_path: Path,
    revision: str,
) -> tempfile.TemporaryDirectory:
    """Read two allow-listed files through a Git object proof of the local pin.

    :param proof_path: Pack with the pinned commit, tree paths and file objects.
    :param revision: Exact commit from the project binding.
    :returns: Temporary workspace holding the authenticated files.
    """
    if not EXACT_SHA.fullmatch(revision):
        raise SchemaGateError("local Standards proof requires an exact revision")
    try:
        with proof_path.open("rb") as source:
            pack = source.read(MAX_STANDARDS_PROOF_BYTES + 1)
    except OSError as exc:
        raise SchemaGateError("local Standards proof file is unavailable") from exc
    if not 0 < len(pack) <= MAX_STANDARDS_PROOF_BYTES:
        raise SchemaGateError("local Standards proof has an invalid size")

    workspace = tempfile.TemporaryDirectory(prefix="redfish_ctl-standards-proof-")
    root = Path(workspace.name)
    objects = root / "objects.git"
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1")

    def git(*args: str, data: bytes | None = None) -> bytes:
        result = subprocess.run(
            ["git", *args], input=data, capture_output=True, check=False, timeout=30, env=env
        )
        if result.returncode:
            raise SchemaGateError("local Standards proof cannot resolve the pinned commit and files")
        return result.stdout

    try:
        git("init", "--quiet", "--bare", str(objects))
        git("-C", str(objects), "index-pack", "--stdin", data=pack)
        actual = git("-C", str(objects), "rev-parse", "--verify", f"{revision}^{{commit}}")
        if actual.decode("ascii").strip() != revision:
            raise SchemaGateError("local Standards proof commit differs from the binding")
        for file in STANDARDS_PROOF_PATHS:
            contents = git("-C", str(objects), "show", f"{revision}:{file}")
            target = root / file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
    except Exception:
        workspace.cleanup()
        raise
    return workspace


def _load_published_standards_proof(
    env: dict[str, str], revision: str
) -> tempfile.TemporaryDirectory:
    """Verify the pin using the exact proof published to this GitLab project.

    :param env: CI variables identifying the project and its approved API.
    :param revision: Exact Standards commit from the project binding.
    :returns: Temporary workspace holding the authenticated manifest and schema.
    """
    if not EXACT_SHA.fullmatch(revision):
        raise SchemaGateError("published Standards proof requires an exact revision")

    api_url = env["REDFISH_STANDARDS_PACKAGE_API"]
    runtime_url = _runtime_ci_base_url(env)
    if runtime_url is None or api_url != f"{runtime_url}/api/v4":
        raise SchemaGateError("Standards proof package API differs from the GitLab job origin")

    api = urlsplit(api_url)
    if api.scheme != "https" or api.hostname is None:
        raise SchemaGateError("Standards proof package API requires HTTPS")
    project_id = env.get("CI_PROJECT_ID", "")
    if not re.fullmatch(r"[1-9][0-9]*", project_id):
        raise SchemaGateError("Standards proof package requires the GitLab project ID")

    path = (
        f"{api.path}/projects/{project_id}/packages/generic/"
        f"local-standards-proof/{revision}/standards.pack"
    )
    conn = HTTPSConnection(api.hostname, port=api.port, timeout=15)
    try:
        conn.request("GET", path, headers={"JOB-TOKEN": env["CI_JOB_TOKEN"]})
        response = conn.getresponse()
        if response.status != 200:
            raise SchemaGateError(f"Standards proof package returned HTTP {response.status}")
        data = response.read(MAX_STANDARDS_PROOF_BYTES + 1)
    except (OSError, HTTPException) as exc:
        raise SchemaGateError("cannot read the Standards proof from the GitLab package API") from exc
    finally:
        conn.close()

    with tempfile.TemporaryDirectory(prefix="redfish_ctl-standards-package-") as work:
        proof_path = Path(work) / "standards.pack"
        proof_path.write_bytes(data)
        return _load_local_standards_proof(proof_path, revision)


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
    expected_jobs = [
        job
        for record in manifest.get("trusted_includes", [])
        for job in record.get("jobs", [])
        if job.get("name") == "project-ci-cpu-validation"
    ]
    if len(expected_jobs) != 1:
        raise SchemaGateError(
            "gates/manifest.yaml must declare project-ci-cpu-validation once"
        )
    expected_job = expected_jobs[0]
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
    if project_command != "./tools/project-ci-cpu-validation.sh":
        raise SchemaGateError("PROJECT_CI_CPU_COMMAND does not select the project CI adapter")
    project_entrypoint = REPO_ROOT / project_command.removeprefix("./")
    if not project_entrypoint.is_file() or not (project_entrypoint.stat().st_mode & 0o111):
        raise SchemaGateError("project CI adapter is missing or not executable")

    include = _load_yaml(provider_root / "ci/templates/project-ci-resource-jobs.yml")
    imported = include.get("project-ci-cpu-validation")
    if not isinstance(imported, dict):
        raise SchemaGateError("bound Builder include lacks project-ci-cpu-validation")
    if imported.get("allow_failure") is not expected_job.get("allowFailure"):
        raise SchemaGateError("Builder project-ci-cpu-validation is not required")
    if not IMMUTABLE_IMAGE.fullmatch(str(imported.get("image", ""))):
        raise SchemaGateError("Builder project-ci-cpu-validation image is not immutable")
    if str(imported.get("image")) != default_image:
        raise SchemaGateError(
            "Builder project-ci-cpu-validation image disagrees with the tracked default"
        )
    if imported.get("stage") != expected_job.get("stage"):
        raise SchemaGateError(
            "Builder project-ci-cpu-validation stage disagrees with the trusted contract"
        )
    if imported.get("tags") != expected_job.get("tags"):
        raise SchemaGateError(
            "Builder project-ci-cpu-validation tags disagree with the trusted contract"
        )
    if imported.get("script") != expected_job.get("script"):
        raise SchemaGateError(
            "Builder project-ci-cpu-validation script disagrees with the trusted contract"
        )
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


def run(env: dict[str, str] | None = None) -> None:
    """Execute the exact-schema validation contract."""
    selected_env = dict(os.environ if env is None else env)
    binding = _load_yaml(REPO_ROOT / "standards-binding.yaml")
    standards_source = binding.get("spec", {}).get("source", {})
    proof = selected_env.get("REDFISH_STANDARDS_PROOF_FILE", "").strip()
    package_api = selected_env.get("REDFISH_STANDARDS_PACKAGE_API", "").strip()
    local_path = str(standards_source.get("localPath", "")).strip()
    local_available = bool(local_path and Path(local_path).expanduser().is_dir())
    using_proof = bool((proof or package_api) and not local_available)
    revision = str(standards_source.get("revision", ""))
    if using_proof and package_api:
        standards_workspace = _load_published_standards_proof(selected_env, revision)
    elif using_proof:
        standards_workspace = _load_local_standards_proof(Path(proof), revision)
    else:
        try:
            standards_base_url = _standards_base_url(standards_source, selected_env)
        except ProviderContractError as exc:
            raise SchemaGateError(str(exc)) from exc
        standards_workspace = _checkout_exact_authority(
            standards_source,
            approved_base_url=standards_base_url,
            env=selected_env,
        )
    try:
        standards_root = Path(standards_workspace.name)
        _validate(
            binding,
            standards_root / "schemas/project-standards-binding.schema.json",
            "standards-binding.yaml",
        )
        declarations = binding["spec"]["providers"]
        if using_proof:
            if declarations:
                raise SchemaGateError("local Standards proof requires a provider-free binding")
            manifest = _load_yaml(standards_root / "manifest.yaml")
            required = [entry["id"] for entry in manifest["spec"]["readOrder"]]
            if binding["spec"]["requiredContracts"] != required:
                raise SchemaGateError("binding contracts differ from the pinned Standards manifest")
        for declaration in declarations:
            if declaration.get("type", "external") != "external":
                continue
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
            try:
                provider_base = _runtime_provider_base_url(REPO_ROOT, selected_env)
            except ProviderContractError as exc:
                raise SchemaGateError(str(exc)) from exc
            provider_workspace = _checkout_exact_authority(
                provider_source,
                approved_base_url=provider_base,
                env=selected_env,
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
    except (
        KeyError,
        OSError,
        ProviderContractError,
        SchemaGateError,
        subprocess.SubprocessError,
        TypeError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as exc:
        print(f"repo.schemas: {exc}", file=sys.stderr)
        return 1
    print("repo.schemas: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
