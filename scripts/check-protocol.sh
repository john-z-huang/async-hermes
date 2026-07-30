#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "$0")/.." && pwd)"
baseline_ref="${HERMES_PROTOCOL_BASELINE_REF:-main}"
cd "$root_dir"

buf lint proto
if git rev-parse --verify --quiet "${baseline_ref}^{commit}" >/dev/null \
  && git ls-tree -r --name-only "$baseline_ref" -- proto | grep -q '\\.proto$'; then
  baseline_dir="$(mktemp -d)"
  trap 'rm -rf "$baseline_dir"' EXIT
  git archive "$baseline_ref" proto | tar -x -C "$baseline_dir"
  buf breaking proto --against "$baseline_dir/proto"
fi
./scripts/generate-protocol.sh
git diff --exit-code -- client/src/generated hermes/interfaces/generated
