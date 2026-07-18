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
./scripts/gates/run.sh <profile>           # the runner (invoked inside a homelab-k8s runner pod)
```

Off-cluster, `check.sh --profile` refuses and prints the in-cluster dispatch (`make k8s-ci REF=<branch>`)
— a gate never runs on a workstation.

## Profiles

- **merge** — merge-request / pre-merge. Static + unit + render. No cluster mutation, no production
  credentials.
- **integration** — needs the cluster; smoke/namespace checks. No BMC mutation.
- **deploy** — live apply. Protected pipeline only, manual, serialized. Never reachable from a
  merge-request pipeline.

## The gates

| id | profile | mutates | what it checks | fails when |
| -- | ------- | ------- | -------------- | ---------- |
| `meta.gate-registry` | merge | no | registry is schema-valid, ids unique, commands exist+executable, mandatory present | any registry inconsistency |
| `meta.ci-runner-tags` | merge | no | every GitLab job carries the `homelab-k8s` tag | a job missing the tag |
| `meta.required-jobs` | merge | no | required jobs exist, no `allow_failure`, no live-apply in an MR pipeline | a required job missing/mis-configured |
| `repo.no-secrets` | merge | no | no committed secrets (gitleaks) | a secret is found, or the scanner is absent |
| `repo.shellcheck` | merge | no | shell scripts pass shellcheck (error severity) | a shell error, or shellcheck absent |
| `repo.format` | merge | no | ruff over files changed vs `origin/main` | a lint finding, or ruff absent |
| `repo.yaml` | merge | no | YAML lints/parses | invalid YAML |
| `repo.schemas` | merge | no | schema-backed docs validate (registry vs its JSON schema) | a schema violation |
| `repo.no-agent-names` | merge | no | no AI-agent identity in tracked content or new commit messages | an agent name appears |
| `repo.no-agent-files` | merge | no | no agent instruction/artifact file is tracked in the published mainline | an agent file is tracked |
| `unit.all` | merge | no | the offline unit suite | any test fails |
| `kubernetes.render` | merge | no | manifests + Helm chart render/parse | a render/parse error |
| `kubernetes.schema` | merge | no | manifests validate against the k8s API schemas (kubeconform) | a schema error, or kubeconform absent |
| `kubernetes.policy` | merge | no | manifest security/best-practice policy (kube-linter) | a policy violation, or the linter absent |
| `integration.namespace` | integration | no | the home cluster is reachable (fail-closed smoke) | cluster unreachable |
| `mutation.plan-required` | deploy | no | a plan artifact exists before apply | no plan produced |
| `mutation.protected-apply` | deploy | **yes** | apply runs only from a protected pipeline | not protected / an MR pipeline |
| `mutation.same-commit` | deploy | no | apply commit == plan commit | plan/apply commits differ |
| `mutation.serialized` | deploy | no | a mutation lock is held (no concurrent apply) | no lock held |
| `mutation.verify-required` | deploy | no | the applied module exposes a verify step | module has no `verify.sh` |
| `mutation.rollback-required` | deploy | no | the applied module exposes a rollback step | module has no `rollback.sh` |
| `evidence.sanitized` | merge | no | the evidence artifact contains no secret material | a secret-shaped token in the artifact |

## Permissions

merge/integration gates run under a **read-only** CI ServiceAccount with no production credentials.
Live apply (deploy profile) runs under a **separate, explicitly selected** apply ServiceAccount, only
from a protected pipeline. See `.internal/SECRET_REGISTRY.md` for credential homes and `k8s/base/` for
the ServiceAccount definitions.

## Failure behavior

Every gate exits non-zero on failure; `scripts/gates/run.sh` stops at the first failure. A gate whose
required tool is absent **fails** (a skipped gate is never an implicit pass). Required CI jobs never use
`allow_failure`, so a red gate blocks the pipeline. Do not claim a gate passed without terminal or
GitLab pipeline evidence.
