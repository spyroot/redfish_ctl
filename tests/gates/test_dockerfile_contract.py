from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"
DOCKER_README = REPO_ROOT / "docker" / "README.md"
README = REPO_ROOT / "README.md"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _effective_ignore_rules(path: Path) -> list[str]:
    """Return non-comment Docker ignore rules.

    :param path: Docker ignore file to parse.
    :return: Ordered non-empty rules with comments removed.
    """
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_production_dockerfile_installs_local_otlp_wheel_as_non_root() -> None:
    """Production image uses a local wheel, the OTLP extra, and a non-root runtime user."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    dockerfile_lower = dockerfile.lower()
    from_lines = [line for line in dockerfile.splitlines() if line.lower().startswith("from ")]

    assert len(from_lines) >= 2
    assert " as builder" in from_lines[0].lower()
    assert "slim" in from_lines[-1].lower()
    assert "--platform=linux/amd64" not in dockerfile_lower
    assert "--platform=linux/arm64" not in dockerfile_lower

    assert "pip wheel" in dockerfile_lower
    assert "--find-links=/wheelhouse" in dockerfile
    assert "--no-index" in dockerfile
    assert '"redfish_ctl[otlp]"' in dockerfile
    assert 'ENTRYPOINT ["redfish_ctl"]' in dockerfile
    assert "USER redfish" in dockerfile


def test_production_dockerfile_header_shows_safe_runtime_examples() -> None:
    """Dockerfile examples cover one-shot CLI use and OTLP exporter use without baked secrets."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    header = "\n".join(dockerfile.splitlines()[:20])

    assert "redfish_ctl system" in header
    assert "exporter --output otlp" in header
    assert "REDFISH_PASSWORD=" not in dockerfile
    assert "IDRAC_PASSWORD=" not in dockerfile
    assert "DOCKERHUB_TOKEN" not in dockerfile


def test_docker_docs_link_the_production_image_usage() -> None:
    """Docker docs explain production-image usage and README links to the Docker guide."""
    docker_readme = DOCKER_README.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    assert "docker/Dockerfile" in docker_readme
    assert "redfish_ctl system" in docker_readme
    assert "exporter --output otlp" in docker_readme
    assert "credentials" in docker_readme.lower()
    assert "[Docker](docker/README.md)" in readme


def test_published_auxiliary_images_use_minimal_context_allowlists() -> None:
    """Published auxiliary images receive only the files copied by their Dockerfiles."""
    expected_rules = {
        "Dockerfile.controller": [
            "**",
            "!pyproject.toml",
            "!setup.py",
            "!requirements.txt",
            "!README.md",
            "!LICENSE",
            "!idrac_ctl/",
            "!idrac_ctl/**",
            "!redfish_ctl/",
            "!redfish_ctl/**",
            "!k8s/",
            "!k8s/controller/",
            "!k8s/controller/**",
            "**/__pycache__/",
            "**/*.py[cod]",
            "**/*.egg-info/",
        ],
        "Dockerfile.mock-bmc": [
            "**",
            "!k8s/",
            "!k8s/sandbox/",
            "!k8s/sandbox/mock_bmc_server.py",
            "!tests/",
            "!tests/supermicro_gb300_corpus.tar.gz",
        ],
    }

    for dockerfile, rules in expected_rules.items():
        ignore_file = REPO_ROOT / "docker" / f"{dockerfile}.dockerignore"
        assert _effective_ignore_rules(ignore_file) == rules


def test_release_auxiliary_images_use_the_guarded_contexts() -> None:
    """Release builds keep the Dockerfile names bound to their matching allowlists."""
    release_workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    for dockerfile in ("Dockerfile.controller", "Dockerfile.mock-bmc"):
        assert f"file: docker/{dockerfile}" in release_workflow
        assert (REPO_ROOT / "docker" / f"{dockerfile}.dockerignore").is_file()
