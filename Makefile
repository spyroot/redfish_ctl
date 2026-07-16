SHELL := /bin/sh
.DEFAULT_GOAL := help

CONDA_ENV ?= redfish_ctl
CONDA ?= $(shell \
	if command -v conda >/dev/null 2>&1; then \
		command -v conda; \
	elif [ -x "$$HOME/miniconda3/condabin/conda" ]; then \
		printf '%s' "$$HOME/miniconda3/condabin/conda"; \
	elif [ -x "$$HOME/miniconda3/bin/conda" ]; then \
		printf '%s' "$$HOME/miniconda3/bin/conda"; \
	else \
		printf '%s' conda; \
	fi)
CONDA_RUN ?= $(CONDA) run -n $(CONDA_ENV)

PYTHON ?= $(CONDA_RUN) python
PYTEST ?= $(CONDA_RUN) pytest
RUFF ?= $(CONDA_RUN) ruff
MYPY ?= $(CONDA_RUN) mypy
TWINE ?= $(CONDA_RUN) twine
DOCKER ?= docker
IMAGE ?= redfish-ctl

.PHONY: help test lint typecheck build bench-concurrency docker-test docker-image docs-voice-check docstring-gate docstring-gate-all k8s-sandbox k8s-consumer k8s-explorer clean gb300-check gb300-image gb300-agent-image gb300-provision gb300-test gb300-lint gb300-gate gb300-shell gb300-clean gb300-push-key

DOCSTRING_BASE ?= origin/main

help: ## Show available developer targets.
	@awk 'BEGIN { \
		FS = ":.*## "; \
		printf "Available targets:\n"; \
	} /^[a-zA-Z0-9_-]+:.*## / { \
		printf "  %-14s %s\n", $$1, $$2; \
	}' $(MAKEFILE_LIST)

test: ## Run the offline pytest suite.
	$(PYTEST) -q

lint: ## Run Ruff over source and tests.
	$(RUFF) check redfish_ctl tests

typecheck: ## Run mypy over source and tests.
	$(MYPY) redfish_ctl tests

build: ## Build sdist/wheel locally and validate package metadata.
	$(PYTHON) setup.py sdist bdist_wheel
	$(TWINE) check dist/*

bench-concurrency: ## Run the opt-in mock-BMC concurrency benchmark.
	$(PYTHON) tests/request_benchmark.py \
		--levels 1,8,32,128 \
		--requests-per-level 128 \
		--concurrency-report reports/concurrency-benchmark.json \
		--summary-report reports/concurrency-benchmark.md

docker-test: ## Build and run the Linux offline test image.
	./docker/run-tests.sh

docker-image: ## Build the production CLI image locally.
	@test -f docker/Dockerfile || { \
		printf '%s\n' 'docker/Dockerfile is not present yet.'; \
		printf '%s\n' 'Add the production image definition before using this target.'; \
		exit 2; \
	}
	$(DOCKER) build -f docker/Dockerfile -t $(IMAGE) .

docs-voice-check: ## Reject first-person wording in public docs.
	! grep -rnE '\b(I|me|my|mine|myself)\b' README.md docs/

docstring-gate: ## Fail if a new/changed method lacks docs (args + return). Override BASE=<ref>.
	$(PYTHON) tools/docstring_gate.py --base $(DOCSTRING_BASE)

docstring-gate-all: ## Fail if ANY method in the tree lacks docs (whole-tree gate; matches CI).
	$(PYTHON) tools/docstring_gate.py --all

k8s-sandbox: ## Run the local Kubernetes read-path sandbox when present.
	./k8s/sandbox/run-sandbox.sh

k8s-sandbox-down: ## Delete the sandbox kind cluster (frees ~1.4GB RAM and steady CPU).
	kind delete cluster --name redfish-sandbox

docker-clean: ## Reclaim local docker space: sandbox cluster, exited redfish containers, dangling layers.
	-kind delete cluster --name redfish-sandbox
	@ids="$$(docker ps -aq --filter status=exited --filter name=redfish)"; \
	 if [ -n "$$ids" ]; then docker rm $$ids >/dev/null; fi
	docker image prune -f

k8s-consumer: ## Build and deploy the fleet-status consumer into the sandbox.
	./k8s/consumer/deploy.sh

k8s-explorer: ## Build and deploy the redfish_ctl web explorer (set REDFISH_IP/SECRET).
	./k8s/explorer/deploy.sh

clean: ## Remove local build, test, and type-check artifacts.
	rm -rf build dist *.egg-info .coverage htmlcov .pytest_cache .ruff_cache .mypy_cache

# ---------------------------------------------------------------------------
# GB300 remote docker test fleet — ALL gates run there, never on a laptop.
# Slot/host resolution comes from scripts/gb300.sh + .internal/gb300-fleet.env
# (gitignored; see TEAM_GUIDE.md "GB300 Docker test environment"). The env
# file is also included here so a locally defined image name (for example an
# internal agent image layered on the public base) wins over the default.
#   SLOT  = fleet slot number (required for the per-slot targets)
#   AGENT = your agent name; isolates your /work volume and container
#   REF   = git ref to test (default main); any pushed branch works
# ---------------------------------------------------------------------------
-include .internal/gb300-fleet.env
GB300_SH    := ./scripts/gb300.sh
GB300_HOSTC  = $$($(GB300_SH) host $(SLOT))
AGENT      ?= $(shell whoami)
REF        ?= main
PYTEST_ARGS ?= -q
# Image choice lives in scripts/gb300.sh: the credentialed agent image when a
# node has it, the public base otherwise; pass GB300_IMAGE=<name> to pin one.

gb300-check: ## Live-check every fleet slot: ssh, docker, base + agent images, disk.
	@printf '%-5s %-22s %-8s %-8s %-8s %-6s\n' SLOT HOST DOCKER BASE AGENT DISK; \
	for s in $$($(GB300_SH) list); do \
		h=$$($(GB300_SH) host $$s); \
		out=$$(ssh -o BatchMode=yes -o ConnectTimeout=5 $$h ' \
			d=no; docker info >/dev/null 2>&1 && d=ok; \
			b=absent; docker image inspect redfish-ctl-dev >/dev/null 2>&1 && b=present; \
			a=absent; docker image inspect redfish-ctl-agent >/dev/null 2>&1 && a=present; \
			df=$$(df -h / | awk "NR==2 {print \$$5}"); \
			echo "$$d $$b $$a $$df"' 2>/dev/null) || out="UNREACHABLE - - -"; \
		printf '%-5s %-22s %-8s %-8s %-8s %-6s\n' "$$s" "$$h" $$out; \
	done

# Both build targets stage the context in a temp file instead of piping it
# straight into ssh: a pipeline's exit status is the last command's, so a
# partial archive failure could otherwise be masked by a succeeding build.
gb300-image: ## Build the public base image on a slot from this checkout's HEAD. SLOT=<n>
	@test -n "$(SLOT)" || { echo "usage: make gb300-image SLOT=<n>"; exit 2; }
	@ctx=$$(mktemp) && \
	git archive --format=tar HEAD > "$$ctx" && \
	ssh $(GB300_HOSTC) \
		'docker build -t redfish-ctl-dev -f docker/Dockerfile.gb300-dev -' < "$$ctx"; \
	rc=$$?; rm -f "$$ctx"; exit $$rc

gb300-agent-image: ## Build the internal agent image (base + staged credentials) on a slot. SLOT=<n>
	@test -n "$(SLOT)" || { echo "usage: make gb300-agent-image SLOT=<n>"; exit 2; }
	@test -f .internal/docker/secrets/git_key || { \
		echo "stage credentials first — see .internal/docker/README.md"; exit 2; }
	@ctx=$$(mktemp) && \
	tar -C .internal/docker -cf "$$ctx" . && \
	ssh $(GB300_HOSTC) \
		'docker build -t redfish-ctl-agent -f Dockerfile.gb300-agent -' < "$$ctx"; \
	rc=$$?; rm -f "$$ctx"; exit $$rc

gb300-provision: ## Build base + agent images on every slot (operator bootstrap).
	@for s in $$($(GB300_SH) list); do \
		echo "=== slot $$s ==="; \
		$(MAKE) gb300-image SLOT=$$s && $(MAKE) gb300-agent-image SLOT=$$s || exit 1; \
	done

gb300-test: ## Run the offline pytest suite on a slot. SLOT=<n> [REF=main] [AGENT=me]
	@test -n "$(SLOT)" || { echo "usage: make gb300-test SLOT=<n> [REF=<branch>]"; exit 2; }
	$(GB300_SH) run $(SLOT) $(AGENT) $(REF) pytest $(PYTEST_ARGS)

# Ruff scope matches the project convention: the tree carries pre-existing
# lint debt, so only files changed vs origin/main are checked (a whole-tree
# run reports ~300 legacy findings and would always fail).
GB300_RUFF_CHANGED = git fetch -q origin main && \
	{ git diff --name-only origin/main...HEAD -- "*.py" | xargs -r ruff check; }

gb300-lint: ## Ruff over files changed vs origin/main, on a slot. SLOT=<n> REF=<branch>
	@test -n "$(SLOT)" || { echo "usage: make gb300-lint SLOT=<n> REF=<branch>"; exit 2; }
	$(GB300_SH) run $(SLOT) $(AGENT) $(REF) sh -c '$(GB300_RUFF_CHANGED)'

gb300-gate: ## Full PR gate on a slot: pytest + changed-files ruff + whole-tree docstring gate.
	@test -n "$(SLOT)" || { echo "usage: make gb300-gate SLOT=<n> [REF=<branch>]"; exit 2; }
	$(GB300_SH) run $(SLOT) $(AGENT) $(REF) sh -c \
		'pytest -q && $(GB300_RUFF_CHANGED) && python tools/docstring_gate.py --all'

gb300-shell: ## Interactive dev shell on a slot (conda env active). SLOT=<n> [AGENT=me]
	@test -n "$(SLOT)" || { echo "usage: make gb300-shell SLOT=<n>"; exit 2; }
	$(GB300_SH) shell $(SLOT) $(AGENT)

gb300-clean: ## Remove exited rfctl containers + dangling layers on a slot. SLOT=<n>
	@test -n "$(SLOT)" || { echo "usage: make gb300-clean SLOT=<n>"; exit 2; }
	ssh $(GB300_HOSTC) ' \
		ids=$$(docker ps -aq --filter status=exited --filter name=rfctl); \
		if [ -n "$$ids" ]; then docker rm $$ids >/dev/null; fi; \
		docker image prune -f'

gb300-push-key: ## Operator only: install the git key + gh token on every slot.
	@test -f "$(HOME)/.ssh/id_rsa" || { echo "no ~/.ssh/id_rsa"; exit 2; }
	@for s in $$($(GB300_SH) list); do \
		h=$$($(GB300_SH) host $$s); \
		scp -o BatchMode=yes -o ConnectTimeout=5 -q ~/.ssh/id_rsa $$h:.ssh/redfish_ctl_git \
			&& gh auth token | ssh $$h 'cat > .ssh/redfish_ctl_gh_token; chmod 600 .ssh/redfish_ctl_git .ssh/redfish_ctl_gh_token' \
			&& echo "slot $$s ($$h): key + token installed" \
			|| echo "slot $$s ($$h): FAILED"; \
	done
