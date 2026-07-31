"""发布构建脚本必须使用项目锁定的工具链。"""

from __future__ import annotations

from pathlib import Path


def test_release_build_uses_locked_toolchains_and_explicit_output() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "build-release.sh").read_text(encoding="utf-8")

    assert "npm run build" in script
    assert "uv run pyinstaller" in script
    assert "--add-data" in script
    assert "release_manifest.json" in script
    assert "scripts/hermes_server_entry.py" in script
    assert "scripts/smoke_release_server.py" in script
    assert "uv run python scripts/package_release.py" in script
    assert '--output-dir "dist/release/$target"' in script
