"""Offline fixtures for the protected simulator delivery stages."""

import hashlib
import json
import tarfile
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from tools import dcloud_simulator as lifecycle

TARGET = lifecycle.Target(
    commit="a" * 40,
    pipeline="101",
    job="123",
    build_namespace="redfish-build",
    namespace="redfish-simulator",
    release="sim",
    harbor="registry.example.test/redfish",
)
DIGEST = f"sha256:{'1' * 64}"
IMAGE_RECEIPT = MappingProxyType({
    "schema": 1,
    "stage": "image",
    "commit": TARGET.commit,
    "pipeline": TARGET.pipeline,
    "job": TARGET.job,
    "repository": TARGET.image_repo,
    "tag": TARGET.commit,
    "digest": DIGEST,
})
DESIRED = MappingProxyType({
    "image": f"{TARGET.image_repo}@{DIGEST}",
    "version": f"0.1.0-ci.g{TARGET.commit}",
    "chartDigest": f"sha256:{'2' * 64}",
})
OBSERVED = MappingProxyType({
    "revision": 1,
    "image": DESIRED["image"],
    "chart": f"dmtf-sim-{DESIRED['version']}",
    "commit": TARGET.commit,
    "readyReplicas": 1,
    "servicePort": 80,
})


@pytest.fixture(autouse=True)
def _isolated_reports(monkeypatch, tmp_path):
    """Store fixture artifacts away from the checkout."""
    monkeypatch.setattr(lifecycle, "REPORTS", tmp_path / "reports")
    lifecycle.REPORTS.mkdir()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", 2, "receipt does not match"),
        ("stage", "chart", "receipt does not match"),
        ("commit", "e" * 40, "receipt does not match"),
        ("pipeline", "202", "receipt does not match"),
        ("tag", "latest", "receipts disagree"),
        ("repository", "registry.example.test/alternate/dmtf-sim", "receipts disagree"),
        ("digest", "sha256:bad", "receipts disagree"),
    ],
)
def test_candidate_refuses_inconsistent_image_receipts(monkeypatch, field, value, message):
    """Change exactly one field in the immutable publication baseline."""
    image = dict(IMAGE_RECEIPT)
    image[field] = value
    (lifecycle.REPORTS / "image.json").write_text(json.dumps(image))
    lifecycle.write_receipt(
        TARGET, "chart", repository=TARGET.chart_repo,
        version=DESIRED["version"], digest=DESIRED["chartDigest"],
    )
    monkeypatch.setattr(lifecycle, "_chart_version", lambda _target: DESIRED["version"])

    with pytest.raises(lifecycle.LifecycleError, match=message):
        lifecycle._candidate(TARGET)


def test_build_context_hashes_real_bundle_and_preserves_exact_source(monkeypatch, tmp_path):
    """Archive only the three tracked build inputs and preserve their bytes."""
    monkeypatch.chdir(tmp_path)
    bundle = Path("fixtures/dmtf.zip")
    bundle.parent.mkdir()
    bundle_bytes = b"fixture-exact-bytes"
    bundle.write_bytes(bundle_bytes)
    sources = {
        str(lifecycle.DOCKERFILE): "FROM image\n\n",
        str(lifecycle.SIMULATOR): "  simulator\n",
        str(lifecycle.CONTRACT): (
            f"binding:\n  bundle:\n    artifact: {bundle}\n"
            f"    sha256: '{hashlib.sha256(bundle_bytes).hexdigest()}'\n"
        ),
    }

    def git_show(*args, **_options):
        assert args[:2] == ("git", "show")
        commit, path = args[2].split(":", 1)
        assert commit == TARGET.commit
        return sources[path]

    monkeypatch.setattr(lifecycle, "run", git_show)
    archive = lifecycle._build_context(tmp_path, TARGET)
    with tarfile.open(archive) as observed:
        assert observed.getnames() == [
            str(lifecycle.DOCKERFILE), str(lifecycle.SIMULATOR), str(bundle)
        ]
        assert observed.extractfile(str(lifecycle.DOCKERFILE)).read() == b"FROM image\n\n"
        assert observed.extractfile(str(bundle)).read() == bundle_bytes

    bundle.write_bytes(b"Git LFS pointer")
    with pytest.raises(lifecycle.LifecycleError, match="unhydrated or disagrees"):
        lifecycle._build_context(tmp_path, TARGET)


def test_existing_image_is_independently_verified_without_rebuild(monkeypatch):
    """An existing exact image produces a no-op receipt."""
    observed = []

    class Registry:
        def __init__(self, target, _env):
            assert target == TARGET

        def image_digest(self, target):
            observed.append(target.commit)
            return DIGEST

    monkeypatch.setattr(lifecycle, "Registry", Registry)
    monkeypatch.setattr(
        lifecycle, "_build_image", lambda *_args: pytest.fail("unexpected rebuild")
    )
    report = lifecycle.publish_image(TARGET, {})
    assert report["reused"] is True
    assert report["digest"] == DIGEST
    assert report["remainingResources"] == []
    assert observed == [TARGET.commit]


@pytest.mark.parametrize(
    ("override", "expected_writes", "message"),
    [
        (None, ("uninstall",), None),
        ({"revision": 2}, (), "modified by another pipeline"),
        ({"commit": "b" * 40}, (), "modified by another pipeline"),
        ({"image": f"{TARGET.image_repo}@sha256:{'9' * 64}"}, (), "modified by another pipeline"),
    ],
)
def test_first_install_rollback_is_scoped_to_observed_apply(
    monkeypatch, override, expected_writes, message,
):
    """Recreate the baseline for each disagreement and guard live rollback."""
    plan = {
        "namespace": TARGET.namespace,
        "release": TARGET.release,
        "desired": dict(DESIRED),
        "rendered": DIGEST,
        "before": None,
    }
    fingerprint = lifecycle._fingerprint(plan)
    lifecycle.write_receipt(TARGET, "plan", plan=plan, fingerprint=fingerprint)
    lifecycle.write_receipt(TARGET, "apply", fingerprint=fingerprint, after=dict(OBSERVED))
    current = [{**OBSERVED, **(override or {})}]
    writes = []

    def mutation(tool, action, *args, **_opts):
        assert tool == "helm"
        assert args[:3] == (TARGET.release, "--namespace", TARGET.namespace)
        writes.append(action)
        current[0] = None

    monkeypatch.setattr(lifecycle, "snapshot", lambda _target: current[0])
    monkeypatch.setattr(lifecycle, "run", mutation)
    monkeypatch.setattr(lifecycle, "_mutation_checks", lambda _target: None)

    if message:
        with pytest.raises(lifecycle.LifecycleError, match=message):
            lifecycle.rollback_release(TARGET, {})
    else:
        report = lifecycle.rollback_release(TARGET, {})
        assert report["before"] == dict(OBSERVED)
        assert report["after"] is None
        assert report["remainingResources"] == []
        repeated = lifecycle.rollback_release(replace(TARGET, job="124"), {})
        assert repeated["reused"] is True
        assert repeated["before"] == repeated["after"] is None
    assert tuple(writes) == expected_writes
