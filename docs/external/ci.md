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
| GitLab | merge request | `merge` profile |
| GitLab | default branch | `merge` plus `integration` profiles |
| GitLab | schedule on the default branch | `merge`, `integration`, and `scheduled` profiles |

## Internal validation paths

The tracked `builder-binding.yaml` file pins the Builder provider revision and
dispatch authority. The `PROJECT_CI_CPU_COMMAND` variable, defined in
`.gitlab-ci.yml`, selects the tracked `tools/project-ci-cpu-validation.sh` adapter;
no operator-created command variable is required. The adapter supports
dependency-free `--help`, non-executing `--dry-run`, and sanitized logging
controls; unknown arguments fail before a gate is selected.

### Wrapper dispatch from the current branch

Install `yq` and `jq`. The `spec.source.localPath` and `spec.source.revision`
fields in `builder-binding.yaml` identify the required clean Builder checkout
and exact commit. The current named branch must already exist in Internal
GitLab at the same exact HEAD commit. The wrapper verifies these prerequisites;
its dry-run default prints the resolved plan without creating a pipeline:

```bash
./scripts/check.sh --profile merge --gate unit.all --dispatch
./scripts/check.sh --profile merge --dispatch
```

If the Builder checkout is not exact and clean, use a separate checkout at the
pinned revision or repair Builder through its pull-request flow. Do not
overwrite a dirty shared checkout.

The first selects one diagnostic gate; the second maps the project `merge`
profile to Builder's complete `full` validation profile. After reviewing that
plan, this explicit apply creates the protected full pipeline, waits, and
returns its terminal pipeline ID, URL, status, and exact commit as JSON:

```bash
./scripts/check.sh --profile merge --dispatch --apply --confirm-project-ci-run
```

Builder reads the project-scoped Internal GitLab credential from its registered
Kubernetes Secret. The consumer wrapper accepts no token value, does not persist
credentials, and does not place them in arguments, output, logs, or evidence.
Optional `--no-wait`, logging, run-ID, and timeout controls pass through to the
bound Builder command. A `--no-wait` receipt is dispatch confirmation only; it
is not terminal gate or merge evidence.

For an unmerged pull request, select the immutable ref produced by Sync Now
instead of the local branch name. The ref suffix must be the exact local HEAD
commit that Builder receives as `requestedCommit`:

```bash
PR_NUMBER=<pull-request-number>
HEAD_SHA="$(git rev-parse HEAD)"
./scripts/check.sh --profile merge --dispatch \
  --ref "sync/pr-${PR_NUMBER}/${HEAD_SHA}"
```

### Internal validation jobs

The exact Builder include defines one required `project-ci-cpu-validation` job.
The dispatcher sets `FOCUSED_GATE` to a merge-profile gate ID from
`gates/manifest.yaml`, such as `unit.all` or `repo.format`; the provider job
runs that one gate through the Kubernetes-guarded `scripts/check.sh` entrypoint.
Builder's `focused` profile selects the same job and defaults to `unit.all`,
while its `full` profile runs the complete merge profile. Selector rules fence
off local merge and project-service jobs, so each provider request has one
validation owner. A focused result cannot replace complete merge evidence.
Merge-request and default-branch pipelines continue to select `gate-merge`
through their normal GitLab rules.

The `k8s-live-check` job, defined in `.gitlab-ci.yml`, is a separate status
probe. An unavailable Kubernetes API is reported as `UNAVAILABLE` and does not
fail that job, so it is not merge evidence or proof of live cluster
availability.

### Manual Internal GitLab API or web dispatch

1. Select the project pipeline on Internal GitLab with the immutable
   `sync/pr-<number>/<40-character-head-sha>` ref produced by the configured
   Sync Now path. The pipeline commit must resolve to that exact head SHA.
2. For diagnostic feedback, unset `PROJECT_CI_PROFILE` and `MERGE_PROFILE`, then
   set `FOCUSED_GATE=unit.all` (or another merge-profile gate ID). The pipeline
   creates only `project-ci-cpu-validation`; the result is diagnostic-only and
   must pass.
3. For merge evidence, unset `PROJECT_CI_PROFILE` and `FOCUSED_GATE`, then set
   `MERGE_PROFILE=merge`. The pipeline must create only `gate-merge`.
4. Verify the terminal job is successful, its commit SHA equals the requested
   head SHA, and its sanitized gate artifacts are available. A focused run ends
   with `run.sh: gate <id> passed`; the authoritative run ends with
   `run.sh: all merge gates passed`.

### Evidence artifacts

Local required jobs publish `reports/ci/<job>.json`, executed gates write
`reports/gates/<gate-id>.json`, and required local smokes publish
`reports/smoke/<job>.json`. The provider-owned CPU job emits terminal status
through the Builder result contract; its current result does not carry an
artifact path. Use the full `gate-merge` artifacts for merge evidence. Evidence
binds the result to the exact project commit, Standards revision, pipeline/job
IDs, and immutable runner digest. Warning/skip counts come from captured gate
output; cleanup comes from tracked-state comparison; exact identity comes from
independent Git read-back; and sanitization is recorded only after a quiet
content scan and atomic file read-back. See [Gates](gates.md#failure-behavior)
for execution and timeout policy.

The upstream access prerequisite for `repo.schemas` is documented in
[Gates](gates.md#selected-gate-summary).

## Protected DMTF simulator deployment

Run the simulator deployment only from the protected default-branch pipeline
in Internal GitLab. The exact provider template is allow-listed by
`trusted_includes` in `gates/manifest.yaml`; see [Trusted provider
includes](gates.md#trusted-provider-includes). The pipeline supplies
`DMTF_RELEASE` and `PROJECT_LIVE_TEST_COMMAND`. The exact Builder template
resolves the registered consumer binding at runtime; that binding supplies
`REDFISH_IP` and `REDFISH_PORT` to the live test.

1. Play `project-service-image-publish` and
   `project-service-chart-publish`. Their exact-commit receipts supply the
   image repository, image digest, chart version, and source commit; the chart
   has no mutable tag fallback.
2. Wait for the non-mutating `project-service-deploy-plan` evidence, then play
   `project-service-deploy`. The deploy job is a protected, serialized manual
   mutation unavailable to merge-request pipelines.
3. Require terminal success from `project-service-verify`,
   `project-service-live-test`, and `project-service-release-evidence`. The
   live test reads `/redfish/v1/` through `RedfishManager`; a pod readiness
   probe or HTTP status alone is not the release evidence.

`project-service-rollback` is the protected manual recovery path. The pull
credential prerequisite is documented under [Private DMTF simulator
image](secrets.md#private-dmtf-simulator-image), and the served resource
contract is [Redfish Simulator Contract](simulator-contract.md).

## Supplemental `.github/workflows/ci.yml` check

Triggers on pushes to `main` and on every pull request.

- Runs the full **offline** test suite (`pytest -q -m "not dmtf_sim_live"`) on
  Python **3.10** for pushes and PRs with non-documentation changes. For a
  docs-only PR, the supplemental fast path scans a bounded top-level test set
  and may run no tests. The Internal GitLab merge profile remains the
  authoritative gate, including nested test coverage.
- Runs `ruff check` as **informational** (reported, not failing — the tree carries pre-existing lint
  debt; new code should still be clean).
- Runs blocking docstring checks with `python tools/docstring_gate.py --base FETCH_HEAD`
  for pull-request changes and `python tools/docstring_gate.py --all` for the
  whole tree.
- Uses **no secrets** and never contacts a BMC. Hardware-backed `live` tests
  skip without the private binding, emulator tests skip without their endpoint
  variables, and `dmtf_sim_live` is excluded. Checkout enables Git LFS for the
  committed offline corpus and specification artifacts used by the suite.

Installs the package with its test dependencies via `pip install -e ".[dev]"` (the `dev` extra pulls
in `pytest`, `requests-mock`, `ruff`, `mypy`, and `numpy`, the last needed for the discovery
`.npy` test).

## `release.yml` — publish on a version tag

Triggers **only** on tags matching `v*` (e.g. `v1.1.2`). A normal push to `main` never publishes.

1. **Verifies the tag matches `redfish_ctl/version.py`** — a mismatch fails before
   any upload.
2. Builds the sdist + wheel and runs `twine check`.
3. Publishes to PyPI via **Trusted Publishing (OIDC)** — no API token is stored anywhere, and PyPI
   records a verified link back to this repo/workflow (this is what makes the project page show
   *verified* details instead of "unverified"). PyPI rejects a duplicate version
   during this publish step; the workflow has no earlier version-existence preflight.
4. Creates a GitHub Release with the artifacts attached.
5. Builds and publishes multi-architecture production images to Docker Hub and
   GitHub Container Registry when `docker/Dockerfile` exists. Controller and
   mock-BMC images are additionally skipped until their own Dockerfiles exist.
   Docker Hub requires repository secrets `DOCKERHUB_USERNAME` and
   `DOCKERHUB_TOKEN`; GitHub Container Registry uses the workflow-provided
   `GITHUB_TOKEN`. Published tags are the release version and `latest`.

The GitHub Release and image jobs currently depend on the build job, not on the
PyPI publish job. A PyPI duplicate-version rejection therefore does not make the
other release surfaces atomic with PyPI.

Complete the one-time publisher and repository-secret setup in
[Installing And Releasing](releasing.md#automated-release-recommended) before
pushing a release tag.

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
