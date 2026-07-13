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

## Automated release (recommended, tokenless)

The going-forward release path is tag-triggered and needs no PyPI token on anyone's machine. It is
driven by `.github/workflows/release.yml` using PyPI **Trusted Publishing** (OIDC), which also makes
PyPI show *verified* project details instead of "unverified".

```bash
python tools/bump_version.py patch      # or minor / major — edits redfish_ctl/version.py only
git add redfish_ctl/version.py
git commit -m "Release 1.1.2"
git push origin main
git tag v1.1.2 && git push origin v1.1.2   # <- this is what publishes
```

On the tag push, the workflow verifies the tag equals `redfish_ctl/version.py` (a mismatch or a
duplicate version fails before upload), builds, `twine check`s, publishes to PyPI via OIDC, and cuts
a GitHub Release with the artifacts attached. `tools/bump_version.py` never runs git itself, so the
tag step stays a deliberate human action.

**One-time PyPI setup** (maintainer, on the web UI): on the `redfish-ctl` project →
*Settings → Publishing → Add a trusted publisher* → GitHub, owner `spyroot`, repo `redfish_ctl`,
workflow `release.yml`. After that, no token is needed to release.

The manual steps below remain valid as a fallback (e.g. before the trusted publisher is configured, or
to publish the one-off `redfish_ctl` deprecation shim under `packaging/redfish_ctl_deprecation/`).

## Release Checklist

Use this order so a broken package does not reach PyPI:

1. Verify the tree.
2. Build source and wheel distributions.
3. Inspect/install the built artifact locally.
4. Upload with `twine`.
5. Tag the release.

## Verify

Run the offline tests with live BMC variables unset:

```bash
env -u REDFISH_IP -u REDFISH_USERNAME -u REDFISH_PASSWORD pytest -q
ruff check <changed files>
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

```bash
git tag "v$(python setup.py --version)"
git push origin --tags
```

## Helper Scripts

- `build_dist.sh`, defined in the repo root, builds `sdist`, installs `check-manifest`, builds wheel
  plus `sdist` again, then uploads `dist/*` with `twine`. It installs `check-manifest` but does not
  run it.
- `build_push.sh`, defined in the repo root, removes `dist/*`, builds `sdist` and wheel, then uploads
  `dist/*` with `twine`.
- `local_install.sh`, defined in the repo root, creates `test1`, builds distributions, and runs
  `python setup.py install`.

Because those scripts can upload, read them before running them.
