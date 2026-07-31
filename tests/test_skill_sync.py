from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-dev-workflow-skill.sh"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_skill_repo(path: Path) -> str:
    """构造一个带真实 HEAD 的临时 git 仓库，返回 HEAD commit。"""
    git(path, "init", "-q")
    git(path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "init")
    return git(path, "rev-parse", "HEAD")


def run_script(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = dict(os.environ)
    base.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)], env=base, capture_output=True, text=True
    )


@pytest.fixture()
def skill_env(tmp_path: Path) -> tuple[Path, Path, str]:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    head = make_skill_repo(skill_dir)
    recorded = tmp_path / "recorded.commit"
    return skill_dir, recorded, head


def test_reports_synced_when_recorded_matches_local_and_remote(
    tmp_path: Path, skill_env: tuple[Path, Path, str]
) -> None:
    skill_dir, recorded, head = skill_env
    recorded.write_text(head, encoding="utf-8")

    proc = run_script(
        {
            "DEV_WORKFLOW_SKILL_DIR": str(skill_dir),
            "DEV_WORKFLOW_RECORDED_FILE": str(recorded),
            "DEV_WORKFLOW_REMOTE_HEAD": head,
        }
    )

    assert proc.returncode == 0
    assert "已同步" in proc.stdout


def test_reports_stale_when_recorded_lags_local_head(
    tmp_path: Path, skill_env: tuple[Path, Path, str]
) -> None:
    skill_dir, recorded, head = skill_env
    # 记录值落后于本地 HEAD
    recorded.write_text("0" * 40, encoding="utf-8")

    proc = run_script(
        {
            "DEV_WORKFLOW_SKILL_DIR": str(skill_dir),
            "DEV_WORKFLOW_RECORDED_FILE": str(recorded),
            "DEV_WORKFLOW_REMOTE_HEAD": head,
        }
    )

    assert proc.returncode == 1
    assert "落后" in proc.stderr


def test_reports_stale_when_remote_advances_beyond_recorded(
    tmp_path: Path, skill_env: tuple[Path, Path, str]
) -> None:
    skill_dir, recorded, head = skill_env
    recorded.write_text(head, encoding="utf-8")

    proc = run_script(
        {
            "DEV_WORKFLOW_SKILL_DIR": str(skill_dir),
            "DEV_WORKFLOW_RECORDED_FILE": str(recorded),
            "DEV_WORKFLOW_REMOTE_HEAD": "f" * 40,
        }
    )

    assert proc.returncode == 1
    assert "落后" in proc.stderr


def test_missing_recorded_file_is_error(tmp_path: Path, skill_env: tuple[Path, Path, str]) -> None:
    skill_dir, _, _ = skill_env
    missing = tmp_path / "missing.commit"

    proc = run_script(
        {
            "DEV_WORKFLOW_SKILL_DIR": str(skill_dir),
            "DEV_WORKFLOW_RECORDED_FILE": str(missing),
            "DEV_WORKFLOW_REMOTE_HEAD": "f" * 40,
        }
    )

    assert proc.returncode == 1
    assert "缺少版本记录文件" in proc.stderr
