#!/usr/bin/env bash
# repo.large-artifact-policy (repository-export, mutates:false): no model weights, disk images, ISOs,
# generated media, or oversized files may be visible at the public boundary.
#
# Two independent checks, because either alone leaks:
#   1. EXTENSION — a 40 MB .safetensors is small by byte count and still must not be published.
#   2. SIZE — an unrecognised extension can still be a multi-gigabyte blob.
#
# Git LFS pointers are the deliberate exception. This repository publishes vendor corpora through LFS
# on purpose (see docs/internal/corpus/CORPA_POLICE.md); a pointer file is ~130 bytes of text, and
# the object it names is what the size limit is about. Blocking pointers would block the corpora the
# project exists to share.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

# 10 GiB, matching the shared contract's stated threshold.
max_bytes=$((10 * 1024 * 1024 * 1024))

blocked_ext='\.(safetensors|ckpt|pt|pth|bin|onnx|gguf|h5|pb|tflite|iso|img|qcow2|vmdk|vdi|dmg|mp4|mov|avi|mkv|wav|flac)$'

fail=0

# git ls-files, not a filesystem walk: only TRACKED content crosses the boundary. An untracked local
# blob is not published and must not fail an export.
while IFS= read -r f; do
    [ -n "$f" ] || continue
    if printf '%s' "$f" | grep -qiE "$blocked_ext"; then
        # An LFS pointer is text; the real object lives in LFS storage and is not the boundary concern.
        if git check-attr filter -- "$f" 2>/dev/null | grep -q 'filter: lfs'; then
            continue
        fi
        echo "repo.large-artifact-policy: blocked artifact type at the public boundary: $f" >&2
        fail=1
    fi
done < <(git ls-files)

while IFS= read -r f; do
    [ -n "$f" ] || continue
    [ -f "$f" ] || continue
    size=$(wc -c < "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt "$max_bytes" ]; then
        echo "repo.large-artifact-policy: $f is ${size} bytes, over the 10 GiB boundary limit" >&2
        fail=1
    fi
done < <(git ls-files)

[ "$fail" -eq 0 ] || exit 1
echo "repo.large-artifact-policy: OK"
