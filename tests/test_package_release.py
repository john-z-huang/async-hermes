"""发布目录装配的确定性与安全边界测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("package_release", Path(__file__).parents[1] / "scripts" / "package_release.py")
assert SPEC and SPEC.loader
package_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_release)


def test_package_release_copies_artifacts_and_records_checksums(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    (root / "hermes").mkdir(parents=True)
    (root / "dist" / "release").mkdir(parents=True)
    (root / "hermes" / "release_manifest.json").write_text(
        '{"release_format_version":1,"release_version":"1.2.3","node_package_version":"1.2.3","python_package_version":"1.2.3","protocol_version":"v1"}',
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text("[project]\nname='hermes'\n", encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "package.json").write_text('{"dependencies":{"ink":"1"},"devDependencies":{}}', encoding="utf-8")
    (root / "package-lock.json").write_text("{}", encoding="utf-8")
    node = tmp_path / "hermes"
    server = tmp_path / "hermes-server"
    node.write_bytes(b"node")
    server.write_bytes(b"server")
    monkeypatch.setattr(package_release, "ROOT", root)

    output = package_release.package_release(
        target="macos-arm64",
        node_executable=node,
        python_server=server,
        output_dir=root / "dist" / "release" / "macos-arm64",
    )

    manifest = json.loads((output / "release-manifest.json").read_text(encoding="utf-8"))
    assert (output / "hermes").read_bytes() == b"node"
    assert (output / "hermes-server").read_bytes() == b"server"
    assert manifest["target"] == "macos-arm64"
    assert [artifact["path"] for artifact in manifest["artifacts"]] == ["hermes", "hermes-server"]
    assert (output / "dependency-inventory.json").is_file()
    assert len((output / "checksums.sha256").read_text(encoding="utf-8").splitlines()) == 4


def test_package_release_rejects_broad_or_invalid_output_paths(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"ok")

    with pytest.raises(ValueError, match="平台标识"):
        package_release.package_release(
            target="../../unsafe",
            node_executable=artifact,
            python_server=artifact,
            output_dir=tmp_path / "dist" / "release" / "unsafe",
        )
    with pytest.raises(ValueError, match="dist/release"):
        package_release.package_release(
            target="macos-arm64",
            node_executable=artifact,
            python_server=artifact,
            output_dir=tmp_path / "outside",
        )
