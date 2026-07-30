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
    subprocess.run([ROOT / "scripts" / "generate-protocol.sh"], cwd=ROOT, check=True)
    subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            "--",
            "client/src/generated",
            "hermes/interfaces/generated",
        ],
        cwd=ROOT,
        check=True,
    )
