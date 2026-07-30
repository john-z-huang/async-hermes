"""schema lint、破坏性检查与生成结果一致性。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]


def protocol_baseline_ref() -> str | None:
    """返回当前 checkout 中可用的协议兼容性基线。"""
    candidates = (
        os.environ.get("HERMES_PROTOCOL_BASELINE_REF"),
        "main",
        "origin/main",
    )
    for ref in dict.fromkeys(candidate for candidate in candidates if candidate):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return ref
    return None


def test_schema_lint_and_breaking_change_check_pass() -> None:
    subprocess.run(["buf", "lint", "proto"], cwd=ROOT, check=True)
    baseline_ref = protocol_baseline_ref()
    if baseline_ref is None:
        return
    baseline_files = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", baseline_ref, "--", "proto"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if any(path.endswith(".proto") for path in baseline_files):
        subprocess.run(
            ["buf", "breaking", "proto", "--against", ".git#branch=" + baseline_ref],
            cwd=ROOT,
            check=True,
        )


def test_protocol_baseline_prefers_ci_reference(
    monkeypatch: object,
) -> None:
    monkeypatch.setenv("HERMES_PROTOCOL_BASELINE_REF", "origin/main")

    assert protocol_baseline_ref() == "origin/main"


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
