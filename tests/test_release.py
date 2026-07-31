"""发布版本契约测试。"""

from __future__ import annotations

from pathlib import Path
import tomllib

from hermes.release import RELEASE_MANIFEST, load_release_manifest


def test_release_manifest_matches_python_project_version() -> None:
    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert RELEASE_MANIFEST.release_format_version == 1
    assert RELEASE_MANIFEST.release_version == project["project"]["version"]
    assert RELEASE_MANIFEST.python_package_version == project["project"]["version"]
    assert load_release_manifest() == RELEASE_MANIFEST
