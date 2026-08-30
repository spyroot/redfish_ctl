"""Release guidance tests for the version bump helper."""

from tools import bump_version


def test_bump_guidance_points_to_canonical_guarded_workflow(
    monkeypatch,
    tmp_path,
    capsys,
):
    """A bump points to one canonical guarded release workflow."""
    version_file = tmp_path / "version.py"
    version_file.write_text("__version__ = '1.2.3'\n", encoding="utf-8")
    monkeypatch.setattr(bump_version, "VERSION_FILE", version_file)

    assert bump_version.main(["patch"]) == 0

    output = capsys.readouterr().out
    assert "git push" not in output
    assert "git tag" not in output
    assert "docs/external/releasing.md#automated-release-recommended" in output
    assert "Release branch: release/v1.2.4" in output
    assert "Version tag: v1.2.4" in output
    assert version_file.read_text(encoding="utf-8") == "__version__ = '1.2.4'\n"
