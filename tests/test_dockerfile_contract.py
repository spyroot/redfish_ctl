from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"
DOCKER_README = REPO_ROOT / "docker" / "README.md"
README = REPO_ROOT / "README.md"


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
