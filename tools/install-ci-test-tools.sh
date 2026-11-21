#!/usr/bin/env bash
# Install the Linux amd64 tools used by the test jobs without requiring root.
set -euo pipefail

install_release_binary() (
  local url=$1 checksum=$2 member=$3 destination=$4 staging
  if [[ $dry_run == true ]]; then
    printf 'Would install %s from %s (sha256 %s)\n' "$destination" "$url" "$checksum"
    exit 0
  fi

  staging=$(mktemp -d "$(dirname "$destination")/.install.XXXXXX")
  trap 'rm -rf -- "$staging"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  if ! curl --fail --silent --show-error --location --retry 2 --max-time 120 \
    --output "$staging/download" "$url" ||
    ! printf '%s  %s\n' "$checksum" "$staging/download" | sha256sum --check --status; then
    printf 'Failed to verify or install %s\n' "$url" >&2
    exit 1
  fi

  if [[ -n $member ]]; then
    tar -xOzf "$staging/download" "$member" > "$staging/binary"
  else
    mv -- "$staging/download" "$staging/binary"
  fi
  chmod 0755 "$staging/binary"
  mv -f -- "$staging/binary" "$destination"
)

dry_run=false
if [[ ${1:-} == --dry-run ]]; then
  dry_run=true
  shift
fi
if [[ $# != 1 || ( $dry_run == false && $(uname -sm) != 'Linux x86_64' ) ]]; then
  printf 'Usage: bash %s [--dry-run] <Linux amd64 bin-directory>\n' "$0" >&2
  exit 2
fi

if [[ $dry_run == false ]]; then
  mkdir -p -- "$1"
fi
install_release_binary \
  https://get.helm.sh/helm-v3.17.3-linux-amd64.tar.gz \
  ee88b3c851ae6466a3de507f7be73fe94d54cbf2987cbaa3d1a3832ea331f2cd \
  linux-amd64/helm "$1/helm"
install_release_binary \
  https://github.com/git-lfs/git-lfs/releases/download/v3.7.0/git-lfs-linux-amd64-v3.7.0.tar.gz \
  e7ebba491af8a54e560be3a00666fa97e4cf2bbbb223178a0934b8ef74cf9bed \
  git-lfs-3.7.0/git-lfs "$1/git-lfs"
install_release_binary \
  https://github.com/jqlang/jq/releases/download/jq-1.8.1/jq-linux-amd64 \
  020468de7539ce70ef1bceaf7cde2e8c4f2ca6c3afb84642aabc5c97d9fc2a0d \
  "" "$1/jq"
install_release_binary \
  https://github.com/mikefarah/yq/releases/download/v4.48.2/yq_linux_amd64 \
  0ffc35320180d4911bc3a772934da508715e08af444cb33d4d43660065e25bcc \
  "" "$1/yq"
