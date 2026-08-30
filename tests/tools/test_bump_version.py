"""Release guidance tests for the version bump helper."""

from tools import bump_version


def test_bump_guidance_preserves_validated_mirror_and_exact_tag(
    monkeypatch,
    tmp_path,
    capsys,
):
    """A bump cannot recommend bypassing the protected internal mainline."""
    version_file = tmp_path / "version.py"
    version_file.write_text("__version__ = '1.2.3'\n", encoding="utf-8")
    monkeypatch.setattr(bump_version, "VERSION_FILE", version_file)

    assert bump_version.main(["patch"]) == 0

    output = capsys.readouterr().out
    assert "git push origin main" not in output
    assert "git push <internal-gitlab-remote> HEAD" not in output
    assert "git push <github-remote> HEAD" in output
    assert "configured Sync Now path" in output
    assert "--confirm-project-ci-run" in output
    assert "exact-head merge profile" in output
    assert "publish-github" in output
    assert 'git tag v1.2.4 <validated-internal-main-sha>' in output
    assert "git push <github-remote> refs/tags/v1.2.4" in output
    assert version_file.read_text(encoding="utf-8") == "__version__ = '1.2.4'\n"
