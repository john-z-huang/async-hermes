"""跨平台发布门禁的最小结构断言。"""

from __future__ import annotations

from pathlib import Path


def test_release_workflow_builds_all_supported_targets_after_cross_language_tests() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "release-build.yml").read_text(encoding="utf-8")

    for target in ("macos-arm64", "linux-x64", "win32-x64"):
        assert target in workflow
    assert "npm run test:all" in workflow
    assert "npm run build:release" in workflow
    assert "actions/upload-artifact@v4" in workflow


def test_release_documentation_covers_install_lifecycle_and_supported_targets() -> None:
    documentation = (Path(__file__).parents[1] / "docs" / "RELEASE.md").read_text(encoding="utf-8")

    for section in ("支持矩阵", "安装", "升级与回滚", "卸载", "故障排查"):
        assert section in documentation
    for target in ("macos-arm64", "linux-x64", "win32-x64"):
        assert target in documentation
