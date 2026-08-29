# Gates

Every mandatory quality/safety gate is registered in `gates/manifest.yaml` (validated by
`schemas/gates.schema.json`) with an **id**, a **profile** (when it runs), a **command** (the
executable), a **required** flag, and a **mutates** classification. `tools/gate_meta.py` (the
meta-gate) keeps the registry and the CI pipeline honest; `tests/gates/` proves it detects a missing,
optional, unregistered, `allow_failure`, mis-tagged, or merge-request-reachable live-apply gate.

## Running gates

Kubernetes is the execution authority. `scripts/check.sh` is the entry point:

```
./scripts/check.sh --list                 # enumerate every registered gate
./scripts/check.sh --profile merge         # run all merge gates (in-cluster only; refuses off-cluster)
```

Off-cluster, `check.sh --profile` refuses; a gate never runs on a workstation.
The configured CI invokes `scripts/gates/run.sh` internally; operators must use
the guarded `scripts/check.sh` entry point.
See [CI/CD Pipeline](ci.md#internal-validation-paths) for guarded dispatch.

## Profiles

- **merge** — merge-request / pre-merge. Static + unit + render. No cluster mutation, no production
  credentials.
- **integration** — needs the cluster; smoke/namespace checks. No BMC mutation.
- **scheduled** — read-only production canaries and drift checks. No BMC mutation.
- **deploy** — live apply. Protected pipeline only, manual, serialized. Never reachable from a
  merge-request pipeline.

## Trusted provider includes

`trusted_includes` in `gates/manifest.yaml` is the only allow-list for
provider-owned GitLab templates. Each entry pins the provider project, exact
40-character commit, template file, permitted hidden templates, and their
mutation classification. A local wrapper must still declare its stage, runner tags, rules, and
`allow_failure: false`, so `tools/gate_meta.py` can reject a floating include,
unknown template, or merge-request-reachable mutation. The protected simulator
job sequence is documented in [CI/CD Pipeline](ci.md#protected-dmtf-simulator-deployment).

## Selected gate summary

This table summarizes the primary operator-facing gates. The exact complete
list comes from `gates/manifest.yaml`; run `./scripts/check.sh --list` to render
it without executing a gate.

| id | profile | mutates | what it checks | fails when |
| -- | ------- | ------- | -------------- | ---------- |
| `meta.gate-registry` | merge | no | registry is schema-valid, ids unique, commands exist+executable, mandatory present | any registry inconsistency |
| `meta.ci-runner-tags` | merge | no | every GitLab job carries the `homelab-k8s` tag | a job missing the tag |
| `meta.required-jobs` | merge | no | required jobs and exact job/smoke artifact paths exist, with no `allow_failure` or MR-reachable live apply | a required job or evidence path is missing/misconfigured |
| `repo.no-secrets` | merge | no | no committed secrets (gitleaks) | a secret is found, or the scanner is absent |
| `repo.shellcheck` | merge | no | shell scripts pass shellcheck (error severity) | a shell error, or shellcheck absent |
| `repo.format` | merge | no | ruff over files changed vs `origin/main` | a lint finding, or ruff absent |
| `repo.yaml` | merge | no | YAML lints/parses | invalid YAML |
| `repo.schemas` | merge | no | the registry and tracked bindings validate against pinned Standards schemas and the pinned Builder provider tree/template fetched with the project job token | a schema, revision, provider-include, or upstream job-token allow-list mismatch |
| `repo.no-agent-names` | merge | no | no AI-agent identity in tracked content or new commit messages | an agent name appears |
| `repo.no-agent-files` | merge | no | no agent instruction/artifact file is tracked in the published mainline | an agent file is tracked |
| `unit.all` | merge | no | the offline unit suite | any test fails |
| `kubernetes.render` | merge | no | manifests + Helm chart render/parse | a render/parse error |
| `kubernetes.schema` | merge | no | manifests validate against the k8s API schemas (kubeconform) | a schema error, or kubeconform absent |
| `kubernetes.policy` | merge | no | manifest security/best-practice policy (kube-linter) | a policy violation, or the linter absent |
| `integration.namespace` | integration | no | the home cluster is reachable (fail-closed smoke) | cluster unreachable |
| `telemetry.full-coverage` | scheduled | no | every cataloged `hw.*` metric has valid Splunk MTS liveness evidence; quiet condition-gated metrics are explicit `NOT_APPLICABLE` | an always-on metric is missing/inactive, or any query/payload is invalid |
| `gitlab.project-token.exists` | integration | no | the CI project token authenticates | token invalid/expired |
| `gitlab.project-token.project-bound` | integration | no | the token is the project bot, bound to its project | not a project-bound bot token |
| `gitlab.project-token.api-access` | integration | no | the token carries API scope | `/version` returns 403 (no api scope) |
| `gitlab.project-token.no-cross-project-access` | integration | no | the token sees only its own project (least privilege) | it can reach other projects |
| `mutation.plan-required` | deploy | no | a plan artifact exists before apply | no plan produced |
| `mutation.protected-apply` | deploy | **yes** | apply runs only from a protected pipeline | not protected / an MR pipeline |
| `mutation.same-commit` | deploy | no | apply commit == plan commit | plan/apply commits differ |
| `mutation.serialized` | deploy | no | a mutation lock is held (no concurrent apply) | no lock held |
| `mutation.verify-required` | deploy | no | the applied module exposes a verify step | module has no `verify.sh` |
| `mutation.rollback-required` | deploy | no | the applied module exposes a rollback step | module has no `rollback.sh` |
| `evidence.sanitized` | merge | no | evidence already present at gate time contains no secret-shaped content; later job and smoke records are scanned before atomic write | the directory is missing, scanning fails, or a secret-shaped token is found |

The `repo.schemas` gate uses the current Internal GitLab project job token to
fetch the pinned Standards schemas and Builder provider tree/template. The
Standards and Builder projects must allow `redfish_ctl` job-token read access; a
missing allow-list entry fails without prompting for credentials.

## Telemetry liveness checks

From a source checkout, inspect selected Splunk MTS metrics or validate the
catalog-driven full-coverage policy without network access:

```bash
python -m tools.splunk_metric_gate hw.power hw.temperature --token-env SPLUNK_API_TOKEN
python -m tools.splunk_full_coverage_gate --dry-run
```

The scheduled full check is read-only and uses the registered realm and
API-scoped token. Run it only in the configured protected CI environment:

```bash
python -m tools.splunk_full_coverage_gate --token-env SPLUNK_API_TOKEN
```

For exact-revision fleet verification, use the build identity check documented in
[Telemetry metrics](telemetry-metrics.md#build-identity-and-fleet-read-back). It requires
the complete host inventory and fails on missing, stale, or mixed `hw.build_info`
identities.

See [Telemetry metrics](telemetry-metrics.md#scheduled-splunk-liveness) for
`always_on` and `condition_gated` semantics.

## Permissions

merge/integration gates run under a **read-only** CI ServiceAccount with no production credentials.
Live apply (deploy profile) runs under a **separate, explicitly selected** apply ServiceAccount, only
from a protected pipeline. See [Secrets](secrets.md) for value-free credential creation and `k8s/base/` for
the ServiceAccount definitions.

## Failure behavior

Every gate exits non-zero on failure; `scripts/gates/run.sh` stops at the first failure. A gate whose
required tool is absent **fails** (a skipped gate is never an implicit pass). Required CI jobs never use
`allow_failure`, so a red gate blocks the pipeline. Do not claim a gate passed without terminal or
GitLab pipeline evidence.

The gate runner writes exact-identity JSON under `reports/gates/`. Required job
and smoke artifacts are defined in
[CI/CD Pipeline](ci.md#evidence-artifacts).
