"""schema lint、破坏性检查与生成结果一致性。"""

from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]


def test_schema_lint_and_breaking_change_check_pass() -> None:
    subprocess.run(["buf", "lint", "proto"], cwd=ROOT, check=True)
    baseline_files = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "main", "--", "proto"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if any(path.endswith(".proto") for path in baseline_files):
        subprocess.run(
            ["buf", "breaking", "proto", "--against", ".git#branch=main"],
            cwd=ROOT,
            check=True,
        )


def test_generation_does_not_change_committed_types() -> None:
    generated_paths = (
        ROOT / "client" / "src" / "generated" / "v1" / "agent.ts",
        ROOT / "hermes" / "interfaces" / "generated" / "v1" / "agent_pb2.py",
        ROOT
        / "hermes"
        / "interfaces"
        / "generated"
        / "v1"
        / "agent_pb2_grpc.py",
    )
    before = {path: path.read_bytes() for path in generated_paths}
    subprocess.run([ROOT / "scripts" / "generate-protocol.sh"], cwd=ROOT, check=True)

    assert {path: path.read_bytes() for path in generated_paths} == before


def test_ci_protocol_check_is_portable_and_fetches_its_baseline() -> None:
    check_script = (ROOT / "scripts" / "check-protocol.sh").read_text(encoding="utf-8")
    workflow = (
        ROOT / ".github" / "workflows" / "protocol-contract.yml"
    ).read_text(encoding="utf-8")

    assert "grep -q" in check_script
    assert "rg -q" not in check_script
    assert "HERMES_PROTOCOL_BASELINE_REF" in check_script
    assert "git archive" in check_script
    assert "fetch-depth: 0" in workflow
    assert "git fetch --no-tags origin main:refs/remotes/origin/main" in workflow
    assert "protobuf-compiler" in workflow
    assert "HERMES_PROTOCOL_BASELINE_REF=origin/main" in workflow


def test_typescript_generation_omits_host_protoc_version() -> None:
    generated_type = (
        ROOT / "client" / "src" / "generated" / "v1" / "agent.ts"
    ).read_text(encoding="utf-8")

    assert "protoc               v" not in generated_type
