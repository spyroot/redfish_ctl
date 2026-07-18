# Secrets and credentials

redfish_ctl needs credentials in a few well-defined places. Every credential has exactly one home and
one creation step — there is never a reason to search the codebase for a token. This page holds the
value-free creation commands for the **Kubernetes** test/controller environment; the container test
fleet stages its secrets as files described in its own runbook.

**Rules that always apply.** Never print, echo, log, or commit a credential value — reference secrets
by name only. Pass tokens through the password manager, an environment variable, or a Kubernetes
`Secret`, never on a command line that a process list would expose. Kubernetes `Secret` objects are
base64, not encrypted at rest by default — treat the cluster's etcd as sensitive.

## Namespaces

The pipeline uses three namespaces; create them once:

```bash
kubectl create namespace rfctl-ci       # unit-test / gate Jobs
kubectl create namespace rfctl-system   # the RedfishEndpoint / NodeProfile controllers + exporter
kubectl create namespace rfctl-runners  # self-hosted CI runners (optional)
```

## BMC credentials (controller + exporter)

The controllers and the exporter authenticate to each server's BMC. Store the credentials once as a
`Secret`; a `RedfishEndpoint` then references it by name, and no pod ever carries the password inline.

```bash
kubectl create secret generic bmc-credentials \
  --namespace rfctl-system \
  --from-literal=username='<bmc-user>' \
  --from-literal=password='<bmc-password>'
```

Rotate in place when the lab re-stages a BMC (roughly every two weeks):

```bash
kubectl create secret generic bmc-credentials -n rfctl-system \
  --from-literal=username='<bmc-user>' --from-literal=password='<new-password>' \
  --dry-run=client -o yaml | kubectl apply -f -
```

## Splunk ingest (exporter → Observability)

The exporter Deployment pushes datapoints to Splunk Observability. It needs an **ingest**-scoped token
(an API-scoped token returns 401 on the ingest endpoint), plus the realm and ingest URL.

```bash
kubectl create secret generic splunk-ingest \
  --namespace rfctl-system \
  --from-literal=token='<ingest-scoped-token>' \
  --from-literal=realm='<realm>' \
  --from-literal=ingestUrl='https://ingest.<realm>.signalfx.com/v2/datapoint'
```

## Image pull (only if the images are private)

The published images (`redfish-ctl`, `redfish-ctl-controller`, `redfish-ctl-mock-bmc`) are intended to
be public on GHCR, so no pull secret is needed. Verify with an anonymous pull before assuming it:

```bash
docker manifest inspect ghcr.io/<owner>/redfish-ctl:latest   # succeeds ⇒ public, no secret needed
```

If a package is private, create a pull secret and attach it to the workload's ServiceAccount:

```bash
kubectl create secret docker-registry ghcr-pull \
  --namespace rfctl-system \
  --docker-server=ghcr.io \
  --docker-username='<github-user>' \
  --docker-password='<GHCR read:packages token>'
kubectl patch serviceaccount default -n rfctl-system \
  -p '{"imagePullSecrets":[{"name":"ghcr-pull"}]}'
```

## Self-hosted CI runners (optional)

Actions Runner Controller authenticates to GitHub with a GitHub App (preferred) or a PAT. As an App:

```bash
kubectl create secret generic arc-github-app \
  --namespace rfctl-runners \
  --from-literal=github_app_id='<app-id>' \
  --from-literal=github_app_installation_id='<installation-id>' \
  --from-file=github_app_private_key='<path-to-app-private-key.pem>'
```

## GitLab runner (self-managed, in-cluster)

The self-managed GitLab Runner (chart values in `platform/gitlab-runner/values.yaml`) registers with a
**runner authentication token** the GitLab project or group issues under Settings → CI/CD → Runners.
Store it as a `Secret`; the chart references it by name, never inline:

```bash
kubectl create secret generic gitlab-runner-token \
  --namespace rfctl-runners \
  --from-literal=runner-token='<runner-authentication-token>'
```

Install the runner against that Secret (URL is set by the operator, not committed):

```bash
helm repo add gitlab https://charts.gitlab.io
helm install gitlab-runner gitlab/gitlab-runner \
  --namespace rfctl-runners \
  -f platform/gitlab-runner/values.yaml \
  --set gitlabUrl='<internal-gitlab-url>' \
  --set runners.secret=gitlab-runner-token
```

## Kubeconfig

Cluster access comes from the operator's kubeconfig (`~/.kube/config`). The tooling targets a single
context by name — set `KUBE_CONTEXT` (default `home-lab-k8s`) or select it once:

```bash
kubectl config use-context <home-cluster-context>
```
