# Gate report (sanitized)

Generated from `gates/manifest.yaml`. Secret-shaped tokens are redacted; this file is
safe to attach as a CI artifact. Status is `-` when the report is registry-only.

| id | profile | mutates | required | status |
| -- | ------- | ------- | -------- | ------ |
| `meta.gate-registry` | merge | False | True | pass |
| `meta.ci-runner-tags` | merge | False | True | pass |
| `meta.required-jobs` | merge | False | True | pass |
| `repo.no-secrets` | merge | False | True | skip |
| `repo.shellcheck` | merge | False | True | skip |
| `repo.format` | merge | False | True | pass |
| `repo.yaml` | merge | False | True | pass |
| `repo.schemas` | merge | False | True | pass |
| `unit.all` | merge | False | True | pass |
| `kubernetes.render` | merge | False | True | skip |
| `kubernetes.schema` | merge | False | True | skip |
| `kubernetes.policy` | merge | False | True | skip |
| `integration.namespace` | integration | False | True | pass |
| `mutation.plan-required` | deploy | False | True | - |
| `mutation.protected-apply` | deploy | True | True | - |
| `mutation.same-commit` | deploy | False | True | - |
| `mutation.serialized` | deploy | False | True | - |
| `mutation.verify-required` | deploy | False | True | - |
| `mutation.rollback-required` | deploy | False | True | - |
| `evidence.sanitized` | merge | False | True | - |

**Summary:** 13 gates reported, 0 failed, 5 skipped.
**Skipped (treated as FAIL):** kubernetes.policy, kubernetes.render, kubernetes.schema, repo.no-secrets, repo.shellcheck
