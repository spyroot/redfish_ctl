#!/usr/bin/env bash
# Publish the GATED internal mainline to the public GitHub mirror. The internal GitLab is the writable
# source of truth; this is the ONLY path outward. It runs after the boundary gates (no-agent-files /
# no-agent-names / no-secrets) and pushes the current commit to GitHub. Requires two CI variables:
#   GITHUB_REPO=owner/name          (e.g. spyroot/redfish_ctl)
#   GITHUB_PUSH_TOKEN               (a MASKED GitLab CI variable — never printed, kept out of the URL)
set -euo pipefail
: "${GITHUB_REPO:?set GITHUB_REPO=owner/name}"
: "${GITHUB_PUSH_TOKEN:?set the masked GITHUB_PUSH_TOKEN CI variable}"

branch="${CI_COMMIT_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
# Auth via an http header (NOT a token-in-URL, which git can echo on error). Header value never logged.
auth="Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_PUSH_TOKEN" | base64 | tr -d '\n')"
git -c http.extraHeader="$auth" push "https://github.com/${GITHUB_REPO}.git" "HEAD:refs/heads/${branch}"
echo "publish-github: pushed ${branch} -> github.com/${GITHUB_REPO}"
