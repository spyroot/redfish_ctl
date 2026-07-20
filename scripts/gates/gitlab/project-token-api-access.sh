#!/usr/bin/env bash
# Gate gitlab.project-token.api-access: validate the GitLab CI project access token (shared, integration
# profile). Backed by tools/gitlab_project_token_gate.py; reads GITLAB_URL / GITLAB_PROJECT_TOKEN /
# GITLAB_PROJECT_ID from the environment (a masked CI variable), never printing the token value.
set -euo pipefail
cd "$(dirname "$0")/../../.."
exec python3 tools/gitlab_project_token_gate.py --check api-access
