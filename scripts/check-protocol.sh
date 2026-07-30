#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root_dir"

buf lint proto
if git ls-tree -r --name-only main -- proto | rg -q '\\.proto$'; then
  buf breaking proto --against '.git#branch=main'
fi
./scripts/generate-protocol.sh
git diff --exit-code -- client/src/generated hermes/interfaces/generated
