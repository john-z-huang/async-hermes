#!/usr/bin/env python3
"""装配 Hermes 的单平台发布目录，并生成可审计的校验与依赖清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TARGET_RE = re.compile(r"^[a-z0-9]+-[a-z0-9_]+$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 根对象无效：{path}")
    return value


def dependency_inventory() -> dict[str, Any]:
    """记录锁文件散列和直接依赖，避免发布构建依赖不可追踪的全局环境。"""
    pyproject = ROOT / "pyproject.toml"
    package_lock = ROOT / "package-lock.json"
    package = _read_json(ROOT / "package.json")
    return {
        "format_version": 1,
        "lockfiles": [
            {"path": "uv.lock", "sha256": sha256(ROOT / "uv.lock")},
            {"path": "package-lock.json", "sha256": sha256(package_lock)},
        ],
        "node": {
            "direct_dependencies": package.get("dependencies", {}),
            "direct_dev_dependencies": package.get("devDependencies", {}),
        },
        "python": {
            "pyproject_sha256": sha256(pyproject),
        },
    }


def _copy_artifact(source: Path, output: Path) -> None:
    if not source.is_file():
        raise ValueError(f"发布产物不存在或不是普通文件：{source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    output.chmod(source.stat().st_mode)


def package_release(*, target: str, node_executable: Path, python_server: Path, output_dir: Path) -> Path:
    """仅从已生成的二进制装配发布目录；覆盖范围由显式 output_dir 决定。"""
    if not TARGET_RE.fullmatch(target):
        raise ValueError("target 必须是形如 macos-arm64 的平台标识。")
    if output_dir.name != target or output_dir.parent.name != "release":
        raise ValueError("发布输出目录必须是 dist/release/<target>。")
    output_dir = output_dir.resolve()
    release_root = (ROOT / "dist" / "release").resolve()
    if release_root not in output_dir.parents:
        raise ValueError("发布输出目录必须位于当前仓库的 dist/release 内。")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    extension = ".exe" if target.startswith("win32-") else ""
    node_output = output_dir / f"hermes{extension}"
    server_output = output_dir / f"hermes-server{extension}"
    _copy_artifact(node_executable, node_output)
    _copy_artifact(python_server, server_output)

    manifest = _read_json(ROOT / "hermes" / "release_manifest.json")
    manifest["target"] = target
    manifest["artifacts"] = [
        {"path": node_output.name, "sha256": sha256(node_output), "size": node_output.stat().st_size},
        {"path": server_output.name, "sha256": sha256(server_output), "size": server_output.stat().st_size},
    ]
    (output_dir / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    inventory = output_dir / "dependency-inventory.json"
    inventory.write_text(json.dumps(dependency_inventory(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksummed = [node_output, server_output, output_dir / "release-manifest.json", inventory]
    (output_dir / "checksums.sha256").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in checksummed), encoding="utf-8"
    )
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="装配 Hermes 平台发布目录")
    parser.add_argument("--target", required=True)
    parser.add_argument("--node-executable", required=True, type=Path)
    parser.add_argument("--python-server", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = package_release(
        target=args.target,
        node_executable=args.node_executable,
        python_server=args.python_server,
        output_dir=args.output_dir,
    )
    print(output)


if __name__ == "__main__":
    main()
