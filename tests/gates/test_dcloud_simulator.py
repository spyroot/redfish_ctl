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
    ("changed", "create_error", "expect_delete"),
    [
        (None, "create timed out", True),
        (("metadata", "labels", "redfish-ctl/build-attempt"), "already exists", False),
        (("spec", "output", "to", "name"), "create timed out", False),
    ],
)
def test_uncertain_build_create_reconciles_only_the_owned_attempt(
    monkeypatch, changed, create_error, expect_delete,
):
    """Use the exact submitted manifest as the baseline for uncertain-create cases."""
    current = None
    deletions = []
    name = f"sim-{TARGET.pipeline}-{TARGET.job}"
    uid = "fixture-created-uid"
    monkeypatch.setattr(lifecycle, "_build_context", lambda folder, _target: folder / "source.tar")

    def tool(*args, data=None, **_options):
        nonlocal current
        if args[:6] == ("oc", "-n", TARGET.build_namespace, "create", "-f", "-"):
            # REST creates the object, but the command never receives its result.
            current = json.loads(data)
            current["metadata"]["uid"] = uid
            if changed is not None:
                field = current
                for component in changed[:-1]:
                    field = field[component]
                field[changed[-1]] = "other-attempt"
            raise lifecycle.LifecycleError(create_error)
        if args[:6] == ("oc", "-n", TARGET.build_namespace, "get", "buildconfig", name):
            return "" if current is None else json.dumps(current)
        if args[:3] == ("oc", "delete", "--raw"):
            assert args[3] == (
                f"/apis/build.openshift.io/v1/namespaces/{TARGET.build_namespace}/buildconfigs/{name}"
            )
            assert args[4:] == ("-f", "-")
            request = json.loads(data)
            assert request["preconditions"] == {"uid": uid}
            assert request["propagationPolicy"] == "Foreground"
            deletions.append(request)
            current = None
            return "{}"
        if args[:5] == ("oc", "-n", TARGET.build_namespace, "get", "build"):
            return '{"items":[]}'
        pytest.fail(f"unexpected tool command {args}")

    monkeypatch.setattr(lifecycle, "run", tool)
    with pytest.raises(lifecycle.LifecycleError, match=create_error):
        lifecycle._build_image(TARGET, "build-push")
    assert bool(deletions) is expect_delete
    assert (current is None) is expect_delete


@pytest.fixture
def _rollback_plan():
    """Seal an exact plan while allowing tests to select its prior release."""
    def seal(prior=None):
        plan = {
            "namespace": TARGET.namespace,
            "release": TARGET.release,
            "desired": dict(DESIRED),
            "rendered": DIGEST,
            "before": prior,
            "values": {
                "provenance": lifecycle._operation_provenance(TARGET),
            },
        }
        fingerprint = lifecycle._fingerprint(plan)
        lifecycle.write_receipt(TARGET, "plan", plan=plan, fingerprint=fingerprint)
        return fingerprint

    return seal


def _owned_helm(status, revision=1):
    """Return submitted Helm metadata and values for the candidate revision."""
    release = {
        "status": status,
        "revision": str(revision),
        "name": TARGET.release,
        "namespace": TARGET.namespace,
        "chart": OBSERVED["chart"],
    }
    values = {
        "provenance": lifecycle._operation_provenance(TARGET),
        "image": {"repository": TARGET.image_repo, "digest": DIGEST},
    }
    return release, values


@pytest.mark.parametrize("status", ["pending-install", "failed", "deployed", "uninstalling"])
def test_first_install_recovers_without_an_apply_receipt(monkeypatch, _rollback_plan, status):
    """Helm-owned revisions recover without a successful apply or ready workload."""
    fingerprint = _rollback_plan()
    assert not (lifecycle.REPORTS / "apply.json").exists()
    release, values = _owned_helm(status)
    current = [release]
    mutations = []
    monkeypatch.setattr(lifecycle, "_mutation_checks", lambda _target: None)
    monkeypatch.setattr(
        lifecycle, "_release_record", lambda _target, **_options: current[0],
    )

    def submitted(target, revision):
        assert target == TARGET
        assert revision == 1
        return values

    def snapshot(_target):
        assert current[0] is None
        return None

    def mutation(tool, action, *args, **_opts):
        assert (tool, action, *args[:3]) == (
            "helm", "uninstall", TARGET.release, "--namespace", TARGET.namespace,
        )
        mutations.append(action)
        current[0] = None

    monkeypatch.setattr(lifecycle, "_release_values", submitted)
    monkeypatch.setattr(lifecycle, "snapshot", snapshot)
    monkeypatch.setattr(lifecycle, "run", mutation)

    report = lifecycle.rollback_release(TARGET, {})
    assert report["fingerprint"] == fingerprint
    assert report["before"]["status"] == status
    assert report["before"]["image"] == DESIRED["image"]
    assert report["after"] is None
    assert report["remainingResources"] == []
    replay = lifecycle.rollback_release(replace(TARGET, job="124"), {})
    assert replay["reused"] is True
    assert replay["before"] == replay["after"] is None
    assert mutations == ["uninstall"]


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("current", "revision", "2", "outside this simulator plan"),
        ("current", "chart", "dmtf-sim-2.0.0", "without this pipeline's provenance"),
        ("current", "status", "unknown", "without this pipeline's provenance"),
        ("provenance", "operationId", "d" * 64, "without this pipeline's provenance"),
        ("provenance", "sourceCommit", "b" * 40, "without this pipeline's provenance"),
        ("image", "digest", f"sha256:{'9' * 64}", "without this pipeline's provenance"),
    ],
)
def test_rollback_rejects_an_unowned_helm_revision(
    monkeypatch, _rollback_plan, section, field, value, message,
):
    """Each disagreement copies the baseline and changes one ownership invariant."""
    _rollback_plan()
    baseline, values = _owned_helm("pending-install")
    current = dict(baseline)
    submitted = {key: dict(record) for key, record in values.items()}
    (current if section == "current" else submitted[section])[field] = value
    monkeypatch.setattr(lifecycle, "_mutation_checks", lambda _target: None)
    monkeypatch.setattr(
        lifecycle, "_release_record", lambda _target, **_options: current,
    )
    monkeypatch.setattr(lifecycle, "_release_values", lambda _target, _revision: submitted)
    monkeypatch.setattr(lifecycle, "snapshot", lambda _target: dict(OBSERVED))
    monkeypatch.setattr(
        lifecycle, "run", lambda *_args, **_options: pytest.fail("unowned rollback attempted"),
    )

    with pytest.raises(lifecycle.LifecycleError, match=message):
        lifecycle.rollback_release(TARGET, {})


def test_partial_upgrade_recovers_only_the_planned_prior_revision(monkeypatch, _rollback_plan):
    """Rollback proves the stored recovery target and independently observes its return."""
    prior = {**OBSERVED, "commit": "b" * 40, "image": f"{TARGET.image_repo}@sha256:{'2' * 64}"}
    _rollback_plan(prior)
    release, values = _owned_helm("pending-upgrade", revision=2)
    prior_values = {
        "provenance": {"sourceCommit": prior["commit"]},
        "image": {"repository": TARGET.image_repo, "digest": f"sha256:{'2' * 64}"},
    }
    reads = []
    mutations = []
    monkeypatch.setattr(lifecycle, "_mutation_checks", lambda _target: None)
    monkeypatch.setattr(lifecycle, "_release_record", lambda _target, **_options: release)

    def stored(target, revision):
        assert target == TARGET
        reads.append(revision)
        return {1: prior_values, 2: values}[revision]

    def mutation(tool, action, *args, **_opts):
        assert (tool, action, *args[:4]) == (
            "helm", "rollback", TARGET.release, "1", "--namespace", TARGET.namespace,
        )
        mutations.append(action)

    def snapshot(_target):
        assert mutations == ["rollback"]
        return {**prior, "revision": 3}

    monkeypatch.setattr(lifecycle, "_release_values", stored)
    monkeypatch.setattr(lifecycle, "run", mutation)
    monkeypatch.setattr(lifecycle, "snapshot", snapshot)

    report = lifecycle.rollback_release(TARGET, {})
    assert reads == [2, 1]
    assert report["before"]["status"] == "pending-upgrade"
    assert report["after"] == {**prior, "revision": 3}
