#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "$0")/.." && pwd)"
python_out="$root_dir/hermes/interfaces/generated/v1"
typescript_out="$root_dir/client/src/generated/v1"

mkdir -p "$python_out" "$typescript_out"

cd "$root_dir"
uv run python -m grpc_tools.protoc \
  -I proto/hermes/v1 \
  --python_out="$python_out" \
  --grpc_python_out="$python_out" \
  proto/hermes/v1/agent.proto

protoc \
  -I proto/hermes/v1 \
  --plugin=protoc-gen-ts_proto="$root_dir/node_modules/.bin/protoc-gen-ts_proto" \
  --ts_proto_out="$typescript_out" \
  --ts_proto_opt=annotateFilesWithVersion=false,env=node,esModuleInterop=true,outputServices=grpc-js \
  proto/hermes/v1/agent.proto
