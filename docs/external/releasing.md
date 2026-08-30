# Installing And Releasing

Author: Mus <spyroot@gmail.com>

`redfish_ctl` is published as the PyPI package named `redfish_ctl`, defined in `setup.py`. The console
entry point installed by that package is also `redfish_ctl`.

## User Install

```bash
python -m pip install redfish_ctl
redfish_ctl --version
redfish_ctl --help
```

For a checkout:

```bash
git clone https://github.com/spyroot/redfish_ctl
cd redfish_ctl
python -m pip install .
redfish_ctl --version
```

## Automated release (recommended)

The release path starts from a change merged through Internal GitLab, followed
by its gated GitHub mirror, and is tag-triggered. PyPI publishing needs no PyPI
token on anyone's machine: `.github/workflows/release.yml` uses **Trusted
Publishing** (OIDC), which also makes PyPI show *verified* project details
instead of "unverified".

```bash
python tools/bump_version.py patch      # or minor / major — edits redfish_ctl/version.py only
git add redfish_ctl/version.py
git commit -m "Release 1.1.2"
# Push a release branch and merge it only after exact-head Internal GitLab gates pass.
# Wait for publish-github to mirror that protected internal main commit, then:
git fetch <github-remote> main
test "$(git rev-parse <github-remote>/main)" = "<validated-internal-main-sha>"
git tag v1.1.2 <validated-internal-main-sha>
git push <github-remote> refs/tags/v1.1.2   # this is what publishes
```

`publish-github`, the protected default-branch job defined in `.gitlab-ci.yml`,
is the only path that updates public GitHub `main`. The tag push publishes the
package, GitHub release, and available container
images as described in [CI/CD Pipeline](ci.md#releaseyml--publish-on-a-version-tag).
`tools/bump_version.py` never runs git, so tagging remains a deliberate human
action.

**One-time PyPI setup** (maintainer, on the web UI): on the `redfish-ctl` project →
*Settings → Publishing → Add a trusted publisher* → GitHub, owner `spyroot`, repo `redfish_ctl`,
workflow `release.yml`. After that, no PyPI token is needed for PyPI
publication.
Before pushing a release tag, configure the Docker Hub repository secrets when
`docker/Dockerfile` is present.

The manual steps below remain valid as a fallback before the trusted publisher is configured.

## Release Checklist

Use this order so a broken package does not reach PyPI:

1. Verify the tree.
2. Build source and wheel distributions.
3. Inspect/install the built artifact locally.
4. Upload with `twine`.
5. Tag the release.

## Verify

Obtain a successful `gate-merge` result for the exact release commit through
the Internal GitLab path documented in [CI/CD Pipeline](ci.md#internal-validation-paths).
The merge profile runs the offline suite and changed-file Ruff gate in the
configured Kubernetes runner; workstation output is not release evidence.

```bash
./scripts/check.sh --profile merge --dispatch --apply --confirm-project-ci-run
```

The single source of truth for the version is `redfish_ctl/version.py` (imported by the CLI for
`--version`); `setup.py` reads that file so the wheel name and the CLI version can never drift.
Confirm the value setup.py will stamp on the artifact:

```bash
python setup.py --version
```

## Build

```bash
python setup.py sdist bdist_wheel
python -m twine check dist/*
```

`twine check`, run by you before upload, verifies the built package metadata and README rendering.

## Local Install Check

Use a throwaway environment:

```bash
conda create -n redfish-ctl-release-test python=3.10
conda activate redfish-ctl-release-test
python -m pip install --upgrade pip setuptools wheel
python -m pip install dist/redfish_ctl-*.whl
redfish_ctl --version
redfish_ctl --help
```

The current `local_install.sh` helper creates a `test1` conda environment, builds `sdist` and wheel,
then runs `python setup.py install`. It does not install the wheel with `pip`, so treat it as a
developer shortcut, not the full release gate above.

## Upload

`TWINE_USERNAME` and `TWINE_PASSWORD`, set by the maintainer shell or `~/.pypirc`, provide PyPI
credentials for `twine upload`.

```bash
python -m twine upload dist/*
```

PyPI versions are immutable. Once uploaded, the same version number cannot be reused.

## Tag

After a manual upload, use the exact validated-and-mirrored commit procedure in
[Automated release](#automated-release-recommended). Do not tag an implicit
working-tree `HEAD` or push unrelated local tags.

## Helper Scripts

- `build_dist.sh`, defined in the repo root, builds `sdist`, installs `check-manifest`, builds wheel
  plus `sdist` again, then uploads `dist/*` with `twine`. It installs `check-manifest` but does not
  run it.
- `build_push.sh`, defined in the repo root, removes `dist/*`, builds `sdist` and wheel, then uploads
  `dist/*` with `twine`.
- `local_install.sh`, defined in the repo root, creates `test1`, builds distributions, and runs
  `python setup.py install`.

Because those scripts can upload, read them before running them.
