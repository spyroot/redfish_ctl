# CI/CD Pipeline

Author: Mus <spyroot@gmail.com>

Internal GitLab is the merge-validation authority. The `gate-merge` job, defined
in `.gitlab-ci.yml`, runs the merge profile on the `homelab-k8s` Kubernetes
runner; a passing GitHub workflow is not merge evidence. The GitHub Actions
workflows described below are supplemental public checks and release automation.
For gate semantics see [Gates](gates.md); for the release procedure, see
[Releasing](releasing.md).

| Source | Trigger | Required path |
|---|---|---|
| GitHub | pull request or push to `main` | offline test workflow |
| GitHub | `v*` tag | release workflow |
| GitLab | merge request or default branch | `merge` profile |
| GitLab | schedule on the default branch | `merge`, `integration`, and `scheduled` profiles |

## Internal validation paths

The `focused-gate` job, defined in `.gitlab-ci.yml`, is available only to
Internal GitLab API or web pipelines. The dispatcher sets `FOCUSED_GATE` to a
merge-profile gate ID from `gates/manifest.yaml`, such as `unit.all` or
`repo.format`; the job runs that one gate through the Kubernetes-guarded
`scripts/check.sh` entrypoint. This exact-commit result is diagnostic evidence
only, not merge or release evidence.

The `gate-merge` job remains the merge authority. For an Internal GitLab API or
web pipeline, the dispatcher sets `MERGE_PROFILE=merge` and omits
`FOCUSED_GATE`; the pipeline then runs the complete merge profile and no
integration, deployment, or publication job. Merge-request and default-branch
pipelines continue to select `gate-merge` through their normal GitLab rules.

### Run internal validation

1. Use the project pipeline on Internal GitLab with the immutable
   `sync/pr-<number>/<40-character-head-sha>` ref produced by the configured
   Sync Now path. The pipeline commit must resolve to that exact head SHA.
2. For diagnostic feedback, set `FOCUSED_GATE=unit.all` (or another
   merge-profile gate ID) and leave `MERGE_PROFILE` unset. The pipeline must
   create only `focused-gate`.
3. For merge evidence, unset `FOCUSED_GATE` and set `MERGE_PROFILE=merge`. The
   pipeline must create only `gate-merge`.
4. Verify the terminal job is successful, its commit SHA equals the requested
   head SHA, and its sanitized gate artifacts are available. A focused run ends
   with `run.sh: gate <id> passed`; the authoritative run ends with
   `run.sh: all merge gates passed`.

Pipeline credentials come from the configured Internal GitLab CI binding; do
not copy tokens into the repository or pass them on the command line.

## Protected DMTF simulator deployment

Run the simulator deployment only from the protected default-branch pipeline
in Internal GitLab. The exact provider template is allow-listed by
`trusted_includes` in `gates/manifest.yaml`; see [Trusted provider
includes](gates.md#trusted-provider-includes). The pipeline supplies
`BUILDER_PROJECT_CONSUMER=redfish_ctl`, `DMTF_RELEASE`, and
`PROJECT_LIVE_TEST_COMMAND`. The provider binding supplies `REDFISH_IP` and
`REDFISH_PORT` to the live test.

1. Play `project-service-image-publish` and
   `project-service-chart-publish`. Their exact-commit receipts supply the
   image repository, image digest, chart version, and source commit; the chart
   has no mutable tag fallback.
2. Wait for `project-service-deploy-plan`, then play
   `project-service-deploy`. Both mutation jobs are protected, serialized, and
   unavailable to merge-request pipelines.
3. Require terminal success from `project-service-verify`,
   `project-service-live-test`, and `project-service-release-evidence`. The
   live test reads `/redfish/v1/` through `RedfishManager`; a pod readiness
   probe or HTTP status alone is not the release evidence.

`project-service-rollback` is the protected manual recovery path. The pull
credential prerequisite is documented under [Private DMTF simulator
image](secrets.md#private-dmtf-simulator-image), and the served resource
contract is [Redfish Simulator Contract](simulator-contract.md).

## Supplemental `ci.yml` check

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
