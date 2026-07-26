# Gates

Every mandatory quality/safety gate is registered in `gates/manifest.yaml` (validated by
`schemas/gates.schema.json`) with an **id**, a **profile** (when it runs), a **command** (the
executable), a **required** flag, and a **mutates** classification. `tools/gate_meta.py` (the
meta-gate) keeps the registry and the CI pipeline honest; `tests/gates/` proves it detects a missing,
optional, unregistered, `allow_failure`, mis-tagged, or merge-request-reachable live-apply gate.

## Running gates

Kubernetes is the execution authority. `scripts/check.sh` is the entry point:

```
./scripts/check.sh --list                           # enumerate every registered gate
./scripts/check.sh --profile merge                  # run the full merge profile in-cluster
./scripts/check.sh --profile merge --gate unit.all  # run one diagnostic gate in-cluster
```

Off-cluster, `check.sh --profile` refuses—a gate never runs on a workstation. To request focused
validation, dispatch a `.gitlab-ci.yml` API/web pipeline at the exact branch commit with its
`FOCUSED_GATE=<gate-id>` variable. Only that file's `focused-gate` job runs. Verify the job's terminal
status and sanitized artifacts against the requested commit. A focused result proves only that gate
at that commit; it is not merge or release evidence. Full merge evidence comes from the required
full pipeline.

## Profiles

- **merge** — merge-request / pre-merge. Static + unit + render. No cluster mutation, no production
  credentials.
- **integration** — needs the cluster; smoke/namespace checks. No BMC mutation.
- **deploy** — live apply. Protected pipeline only, manual, serialized. Never reachable from a
  merge-request pipeline.

## Registered gates

`gates/manifest.yaml` is the canonical gate catalog. Run `./scripts/check.sh --list` to read each
gate's id, profile, mutation classification, and command. Keeping the catalog in the registry avoids
a second hand-maintained table drifting from required CI behavior.

## Permissions

merge/integration gates run under a **read-only** CI ServiceAccount with no production credentials.
Live apply (deploy profile) runs under a **separate, explicitly selected** apply ServiceAccount, only
from a protected pipeline. See [secret setup](secrets.md) and the
[Kubernetes base](../../k8s/base/) for the ServiceAccount definitions.
