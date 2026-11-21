# CI/CD Pipeline

Author: Mus <spyroot@gmail.com>

GitHub hosts pull requests and the source branch. DCloud GitLab pulls that
branch and runs `gate-merge`, defined in `.gitlab-ci.yml`. Merge requires
GitHub's `test (3.10)` and DCloud's `gate-merge` for the same PR head.
See [Gates](gates.md) and [Releasing](releasing.md).

| Source | Trigger | Required path |
|---|---|---|
| GitHub | Pull request or push to `main` | Offline tests and coverage |
| GitHub | `v*` tag | Release workflow |
| DCloud | Mirrored branch update or merge request | Merge profile |
| DCloud | Protected default branch | Merge and integration profiles |
| DCloud | Schedule on the protected default branch | Merge, integration, and scheduled profiles |

## DCloud runners and toolbox

`runner.yaml` declares the immutable Harbor toolbox image and the project
runner tags. The focused runner selects `redfish_ctl-focused` for merge and
diagnostic work. The protected runner selects `redfish_ctl-protected` for
integration, scheduled checks, and the simulator lifecycle. Both require
`redfish_ctl`.

`docker/Dockerfile.ci-toolbox` installs the tools listed in `toolbox.yaml`.
Build it in the dedicated `redfish-ctl-build` OpenShift namespace when those
inputs change, verify the published digest, and update the pin in
`runner.yaml` and `.gitlab-ci.yml` together. Jobs create the project Conda
environment from `environment.yml` and fetch Git LFS fixtures from GitHub.

The `repo.schemas` gate uses the pinned files from a local Standards checkout.
In DCloud, the same gate authenticates the minimal Git object proof published
to the project's private Package Registry under the pinned revision. It
requires the exact commit, binding schema, and full contract list from the
authenticated manifest. See [Gates](gates.md#selected-gate-summary).

## Dispatch an exact branch check

Authenticate `glab` to the configured DCloud project and install `jq` and
`yq`. The branch selected by `--ref`, or the current named branch by default,
must already have the same commit on DCloud. These commands print a plan:

```bash
./scripts/check.sh --profile merge --gate unit.all --dispatch
./scripts/check.sh --profile merge --dispatch
```

Run the second plan to collect the required merge result:

```bash
./scripts/check.sh --profile merge --dispatch --apply --confirm-project-ci-run
```

The wrapper checks the mirror SHA before dispatch and verifies the returned
pipeline SHA. It waits for the terminal result unless `--no-wait` is supplied.
For a successful result it reads back the exact job and its gate evidence.
An API or web dispatch with `MERGE_PROFILE=merge` selects `gate-merge`.
Set `FOCUSED_GATE=unit.all` instead to select only
`project-ci-cpu-validation`, the CPU job defined in `.gitlab-ci.yml`.

### Evidence

Required jobs publish `reports/ci/<job>.json` and
`reports/smoke/<job>.json`. Individual gates write
`reports/gates/<gate-id>.json`. Accept a merge result only when the required
job succeeds and its evidence binds the project SHA, Standards revision,
pipeline and job IDs, and toolbox digest. Gate evidence rejects warnings,
required skips, secret-shaped output, and untracked residue outside its
declared output. See [Gates](gates.md#failure-behavior).

## Protected DMTF simulator deployment

The simulator jobs are declared in `.gitlab-ci.yml` and fenced to the
protected default branch. `REDFISH_BUILD_NAMESPACE` and
`REDFISH_SIM_NAMESPACE`, defined there, select the project's dedicated
OpenShift build and simulator namespaces. The protected job identity needs
access to start and read native builds and manage the simulator release.
The build uses the project Harbor push Secret. Helm operations use the
project-scoped protected Harbor credentials. The simulator values select
the project pull Secret and allow ingress from the Redfish runner namespaces.

1. Play `project-service-image-publish` and `project-service-chart-publish`.
   Their receipts identify the source commit and immutable image and chart.
2. Review `project-service-deploy-plan`, then play the serialized
   `project-service-deploy` job. It checks the same-commit plan before applying.
3. Require terminal success from `project-service-verify`,
   `project-service-live-test`, and `project-service-release-evidence`.
   These jobs read the running image digest and exercise `/redfish/v1/`
   against the actual service.
4. Use `project-service-rollback` for recovery. The job shares the deployment
   lock, verifies the installed operation against the plan, and checks the
   restore or cleanup even if apply stopped before writing its receipt.

`publish-github`, the publication-boundary job in `.gitlab-ci.yml`, retains
the export safety scans and verifies the source SHA on GitHub. DCloud pulls
from GitHub. After merging, compare both `main` branch tips.

## Tests and coverage on GitHub and DCloud

`.github/workflows/ci.yml` runs the offline Python 3.10 suite. The DCloud
`gate-merge` job collects coverage from the same `unit.all` gate while
executing the full merge profile. Both publish the tested commit, JUnit, and
Cobertura reports under `reports/tests/`. GitHub retains them in
`test-reports-3.10` and GitLab exposes them on the job.

The offline suite clears BMC connection variables and excludes hardware,
emulator, deployed simulator, and externally provisioned schema tests.
The required `gate-merge` and protected jobs remain in `.gitlab-ci.yml`.

The GitHub workflow also performs these checks.

- Runs `ruff check` as **informational** (reported, not failing — the tree carries pre-existing lint
  debt; new code should still be clean).
- Runs blocking docstring checks with `python tools/docstring_gate.py --base FETCH_HEAD`
  for pull-request changes and `python tools/docstring_gate.py --all` for the
  whole tree.

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
