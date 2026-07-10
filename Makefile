SHELL := /bin/sh
.DEFAULT_GOAL := help

PYTHON ?= python
PYTEST ?= pytest
RUFF ?= ruff
MYPY ?= mypy
TWINE ?= twine
DOCKER ?= docker
IMAGE ?= redfish-ctl

.PHONY: help test lint typecheck build docker-test docker-image k8s-sandbox clean

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

docker-test: ## Build and run the Linux offline test image.
	./docker/run-tests.sh

docker-image: ## Build the production CLI image locally.
	@test -f docker/Dockerfile || { \
		printf '%s\n' 'docker/Dockerfile is not present yet.'; \
		printf '%s\n' 'Add the production image definition before using this target.'; \
		exit 2; \
	}
	$(DOCKER) build -f docker/Dockerfile -t $(IMAGE) .

k8s-sandbox: ## Run the local Kubernetes read-path sandbox when present.
	@test -f k8s/sandbox/kind-config.yaml || { \
		printf '%s\n' 'k8s/sandbox/kind-config.yaml is not present yet.'; \
		printf '%s\n' 'Add the sandbox manifests and smoke script before using this target.'; \
		exit 2; \
	}
	@printf '%s\n' 'Run the sandbox smoke harness from k8s/sandbox/.'

clean: ## Remove local build, test, and type-check artifacts.
	rm -rf build dist *.egg-info .coverage htmlcov .pytest_cache .ruff_cache .mypy_cache
