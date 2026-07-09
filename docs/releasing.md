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

## Release Checklist

I use this order so a broken package does not reach PyPI:

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

Check the version in `setup.py`, the single packaging source of truth:

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
then runs `python setup.py install`. It does not install the wheel with `pip`, so I treat it as a
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
