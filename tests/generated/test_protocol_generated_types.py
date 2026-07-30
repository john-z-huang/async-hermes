"""跨语言生成类型的可执行契约。"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[2]


def run_generation() -> None:
    subprocess.run([ROOT / "scripts" / "generate-protocol.sh"], cwd=ROOT, check=True)


def test_python_generated_messages_and_grpc_service_are_importable() -> None:
    run_generation()
    generated_path = ROOT / "hermes" / "interfaces" / "generated" / "v1"
    sys.path.insert(0, str(generated_path))
    try:
        import agent_pb2
        import agent_pb2_grpc
    finally:
        sys.path.pop(0)

    assert agent_pb2.DESCRIPTOR.package == "hermes.v1"
    assert agent_pb2.AgentEvent.DESCRIPTOR.oneofs_by_name["payload"]
    assert hasattr(agent_pb2_grpc, "HermesAgentStub")
    assert hasattr(agent_pb2_grpc, "HermesAgentServicer")


def test_typescript_generated_types_pass_strict_typecheck() -> None:
    run_generation()
    subprocess.run(
        ["npm", "run", "typecheck:protocol"], cwd=ROOT, check=True
    )
