"""Agent 响应文件的安全持久化。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .workspace_tools import resolve_workspace


def resolve_output_file(
    output: str,
    *,
    workspace: str | Path | None = None,
    output_file: str | Path | None = None,
) -> Path:
    """返回 workspace 内安全的绝对输出路径。"""
    workspace_path = resolve_workspace(workspace)
    if output_file is None:
        response_hash = hashlib.sha256(output.encode("utf-8")).hexdigest()[:16]
        relative_path = Path(".agents") / f"response-{response_hash}.txt"
    else:
        relative_path = Path(output_file).expanduser()
        if relative_path.is_absolute():
            raise ValueError("output-file 必须是 workspace 内的相对路径。")
    output_path = (workspace_path / relative_path).resolve()
    if output_path == workspace_path or not output_path.is_relative_to(workspace_path):
        raise ValueError("output-file 必须指向 workspace 内的文件。")
    return output_path


def persist_agent_output(
    output: str,
    *,
    workspace: str | Path | None = None,
    output_file: str | Path | None = None,
) -> Path:
    """持久化最终 Agent 输出并返回绝对路径。"""
    output_path = resolve_output_file(
        output, workspace=workspace, output_file=output_file
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8")
    return output_path
