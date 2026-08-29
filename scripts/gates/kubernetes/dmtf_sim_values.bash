#!/usr/bin/env bash
# Sourceable deterministic image identity for offline DMTF simulator chart gates.

dmtf_sim_set_helm_values() {
  local source_commit="${1:?source commit is required}"
  local image_repository="registry.invalid/redfish/dmtf-sim"
  local image_digest="sha256:$(printf '0%.0s' {1..64})"

  DMTF_SIM_HELM_VALUES=(
    --set-string "provenance.sourceCommit=$source_commit"
    --set-string "image.repository=$image_repository"
    --set-string "image.digest=$image_digest"
  )
}
