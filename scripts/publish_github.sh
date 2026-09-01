#!/usr/bin/env bash
# Publish the GATED internal mainline to the public GitHub mirror. This outbound mirror runs after
# the configured repository-export and publication boundary gates, then pushes the current commit
# to GitHub. It does not replace the GitHub pull-request entry path. Requires two CI variables:
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
