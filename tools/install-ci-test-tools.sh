#!/usr/bin/env bash
# Install the Linux amd64 tools used by the test jobs without requiring root.
set -euo pipefail

install_release_binary() {
  local url=$1 checksum=$2 member=$3 destination=$4 archive
  archive=$(mktemp)
  if ! (
    curl --fail --silent --show-error --location --retry 2 --max-time 120 \
      --output "$archive" "$url" &&
      printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status &&
      tar -xOzf "$archive" "$member" > "$destination" &&
      chmod 0755 "$destination"
  ); then
    rm -f -- "$archive" "$destination"
    printf 'Failed to verify or install %s\n' "$url" >&2
    return 1
  fi
  rm -f -- "$archive"
}

if [[ $# != 1 || $(uname -sm) != 'Linux x86_64' ]]; then
  printf 'Usage on Linux amd64: bash %s <bin-directory>\n' "$0" >&2
  exit 2
fi

mkdir -p -- "$1"
install_release_binary \
  https://get.helm.sh/helm-v3.17.3-linux-amd64.tar.gz \
  ee88b3c851ae6466a3de507f7be73fe94d54cbf2987cbaa3d1a3832ea331f2cd \
  linux-amd64/helm "$1/helm"
install_release_binary \
  https://github.com/git-lfs/git-lfs/releases/download/v3.7.0/git-lfs-linux-amd64-v3.7.0.tar.gz \
  e7ebba491af8a54e560be3a00666fa97e4cf2bbbb223178a0934b8ef74cf9bed \
  git-lfs-3.7.0/git-lfs "$1/git-lfs"
