SHELL := /bin/sh
.DEFAULT_GOAL := help

PYTHON ?= python
PYTEST ?= pytest
RUFF ?= ruff
MYPY ?= mypy
TWINE ?= twine
DOCKER ?= docker
IMAGE ?= redfish-ctl

.PHONY: help test lint typecheck build bench-concurrency docker-test docker-image docs-voice-check k8s-sandbox clean

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

k8s-sandbox: ## Run the local Kubernetes read-path sandbox when present.
	./k8s/sandbox/run-sandbox.sh

clean: ## Remove local build, test, and type-check artifacts.
	rm -rf build dist *.egg-info .coverage htmlcov .pytest_cache .ruff_cache .mypy_cache
