"""发布版本契约的单一 Python 读取入口。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
from typing import Any


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """Node、Python 和 Protocol 必须共同遵守的发布元数据。"""

    release_format_version: int
    release_version: str
    node_package_version: str
    python_package_version: str
    protocol_version: str


def _required_string(values: dict[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"发布清单缺少有效字段：{key}")
    return value


def load_release_manifest() -> ReleaseManifest:
    """读取随 Python 包分发的静态版本清单，并在格式损坏时快速失败。"""
    try:
        raw = json.loads(files("hermes").joinpath("release_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("无法读取 Hermes 发布清单。") from error
    if not isinstance(raw, dict) or raw.get("release_format_version") != 1:
        raise RuntimeError("发布清单格式不受支持。")
    return ReleaseManifest(
        release_format_version=1,
        release_version=_required_string(raw, "release_version"),
        node_package_version=_required_string(raw, "node_package_version"),
        python_package_version=_required_string(raw, "python_package_version"),
        protocol_version=_required_string(raw, "protocol_version"),
    )


RELEASE_MANIFEST = load_release_manifest()
