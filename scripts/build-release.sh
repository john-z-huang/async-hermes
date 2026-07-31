#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "$0")/.." && pwd)"
target="${1:?用法：scripts/build-release.sh <target>}"
work_dir="$(mktemp -d)"
data_separator="$(uv run python -c 'import os; print(os.pathsep)')"
trap 'rm -rf "$work_dir"' EXIT

cd "$root_dir"
npm run build
node_executable="dist/hermes"
if [[ "$target" == win32-* && -f "dist/hermes.exe" ]]; then node_executable="dist/hermes.exe"; fi
uv run pyinstaller --clean --noconfirm --onefile --name hermes-server \
  --distpath "$work_dir/dist" \
  --workpath "$work_dir/work" \
  --specpath "$work_dir/spec" \
  --add-data "$root_dir/hermes/release_manifest.json${data_separator}hermes" \
  --collect-all grpc \
  --collect-all agents \
  scripts/hermes_server_entry.py
uv run python scripts/package_release.py \
  --target "$target" \
  --node-executable "$node_executable" \
  --python-server "$work_dir/dist/hermes-server" \
  --output-dir "dist/release/$target"
server_suffix=""
if [[ "$target" == win32-* ]]; then server_suffix=".exe"; fi
uv run python scripts/smoke_release_server.py --server "dist/release/$target/hermes-server$server_suffix"
