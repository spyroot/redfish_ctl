# CI/CD Pipeline

Author: Mus <spyroot@gmail.com>

Two GitHub Actions workflows run this project's automation. Both live in `.github/workflows/`. This
doc describes what they do and the runner they use; for the step-by-step *release* procedure see
[Releasing](releasing.md).

## `ci.yml` — test + lint on every change

Triggers on pushes to `main` and on every pull request.

- Runs the **offline** test suite (`pytest -q`) on a matrix of Python **3.10, 3.11, 3.12**.
- Runs `ruff check` as **informational** (reported, not failing — the tree carries pre-existing lint
  debt; new code should still be clean).
- Uses **no secrets**, never contacts a BMC (live `@pytest.mark.live` tests auto-skip with no
  `REDFISH_IP`), and does **not** fetch Git LFS (the offline suite reads JSON fixtures only, never the
  LFS-tracked firmware binaries).

Installs the package with its test dependencies via `pip install -e ".[dev]"` (the `dev` extra pulls
in `pytest`, `requests-mock`, `ruff`, `mypy`, and `numpy`, the last needed for the discovery
`.npy` test).

## `release.yml` — publish on a version tag

Triggers **only** on tags matching `v*` (e.g. `v1.1.2`). A normal push to `main` never publishes.

1. **Verifies the tag matches `redfish_ctl/version.py`** — a mismatch or a duplicate version fails
   before any upload.
2. Builds the sdist + wheel and runs `twine check`.
3. Publishes to PyPI via **Trusted Publishing (OIDC)** — no API token is stored anywhere, and PyPI
   records a verified link back to this repo/workflow (this is what makes the project page show
   *verified* details instead of "unverified").
4. Creates a GitHub Release with the artifacts attached.

One-time maintainer setup on PyPI (Project → Settings → Publishing → Add a trusted publisher): owner
`spyroot`, repo `redfish_ctl`, workflow `release.yml`. After that, releasing is just
`tools/bump_version.py` → commit → push a `vX.Y.Z` tag; see [Releasing](releasing.md).

The one-off `redfish_ctl` deprecation shim (`packaging/redfish_ctl_deprecation/`) is **not** automated —
it is published manually and rarely.

## The runner and Node.js

Jobs run on GitHub's `ubuntu-latest` hosted runner. Node.js appears **only** here, and only because
GitHub Actions executes JavaScript-authored actions on a Node runtime the runner provides — it is not
part of `redfish_ctl` and users never need it. Each stock action (`actions/checkout`,
`actions/setup-python`, `actions/upload-artifact`, `actions/download-artifact`,
`softprops/action-gh-release`) declares its Node version in its own `action.yml`; we currently pin
versions that target **node24**. Our own workflow *steps* (`pytest`, `ruff`, `python -m build`) run
Python, not Node.

**Maintenance:** if a run logs "Node.js NN is deprecated", bump the affected `uses:` actions to a
newer major whose `action.yml` says `using: node24` (or later). That is a workflow-file change only.
