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

.PHONY: help test lint typecheck build bench-concurrency docker-test docker-image docs-voice-check docstring-gate docstring-gate-all k8s-sandbox k8s-consumer k8s-explorer clean

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
