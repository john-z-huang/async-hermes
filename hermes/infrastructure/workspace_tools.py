"""受 workspace 边界保护的只读工具。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import shlex

from agents import FunctionTool, function_tool


@dataclass(frozen=True)
class AgentPermissions:
    """Agent 在 workspace 内可使用的 shell 权限。"""

    allowed_commands: frozenset[str]
    allowed_git_subcommands: frozenset[str]
    forbidden_arguments: frozenset[str]
    forbidden_argument_prefixes: tuple[str, ...]


DEFAULT_AGENT_PERMISSIONS = AgentPermissions(
    allowed_commands=frozenset(
        {"cat", "find", "git", "head", "ls", "pwd", "rg", "tail", "wc"}
    ),
    allowed_git_subcommands=frozenset(
        {"branch", "diff", "grep", "log", "ls-files", "rev-parse", "show", "status"}
    ),
    forbidden_arguments=frozenset(
        {
            "--exec", "--execdir", "--in-place", "--ok", "--okdir", "--pre",
            "--git-dir", "--work-tree", "--write", "&&", "&", "-delete",
            "-exec", "-execdir", "-i", "-ok", "-okdir", ";", "|", "||",
        }
    ),
    forbidden_argument_prefixes=(
        "--config=", "--git-dir=", "--pre=", "--work-tree=",
    ),
)
PERMISSION_PROFILES = {"read-only": DEFAULT_AGENT_PERMISSIONS}


def resolve_workspace(workspace: str | Path | None) -> Path:
    """返回现有 workspace 的绝对路径。"""
    resolved = (
        Path.cwd().resolve()
        if workspace is None
        else Path(workspace).expanduser().resolve()
    )
    if not resolved.is_dir():
        raise ValueError(f"workspace 必须是已存在的目录：{resolved}")
    return resolved


def resolve_permissions(permissions: str | AgentPermissions) -> AgentPermissions:
    """解析命名权限配置或显式权限对象。"""
    if isinstance(permissions, AgentPermissions):
        return permissions
    try:
        return PERMISSION_PROFILES[permissions]
    except KeyError as error:
        available = ", ".join(PERMISSION_PROFILES)
        raise ValueError(
            f"未知 permissions 配置 {permissions!r}；当前可用值：{available}"
        ) from error


def validate_read_only_command(
    command: Sequence[str],
    workspace: str | Path | None = None,
    permissions: AgentPermissions = DEFAULT_AGENT_PERMISSIONS,
) -> str | None:
    """命令超出只读边界时返回错误信息。"""
    if not command:
        return "拒绝执行：未提供命令。"
    workspace_path = resolve_workspace(workspace)
    executable, *arguments = command
    if executable not in permissions.allowed_commands:
        return f"拒绝执行：{executable!r} 不在只读命令白名单中。"
    if any(argument in permissions.forbidden_arguments for argument in arguments):
        return "拒绝执行：命令包含可能修改文件或执行其他命令的参数。"
    if any(
        argument.startswith(permissions.forbidden_argument_prefixes)
        for argument in arguments
    ):
        return "拒绝执行：命令包含不允许的配置或执行参数。"
    for argument in arguments:
        path_value = argument.partition("=")[2] if "=" in argument else argument
        if path_value == ".." or path_value.startswith("../"):
            return "拒绝执行：只允许访问 workspace 目录内的路径。"
        candidate_path = Path(path_value)
        if not candidate_path.is_absolute():
            candidate_path = workspace_path / candidate_path
        if not candidate_path.resolve().is_relative_to(workspace_path):
            return "拒绝执行：只允许访问 workspace 目录内的路径。"
    if executable == "git":
        subcommand = next(
            (argument for argument in arguments if not argument.startswith("-")), None
        )
        if subcommand not in permissions.allowed_git_subcommands:
            return "拒绝执行：只允许只读 Git 子命令。"
    return None


async def execute_read_only_shell(
    commands: Sequence[str],
    *,
    workspace: str | Path | None = None,
    permissions: AgentPermissions = DEFAULT_AGENT_PERMISSIONS,
) -> str:
    """在 workspace 内执行白名单中的只读命令。"""
    workspace_path = resolve_workspace(workspace)
    outputs: list[str] = []
    for command_text in commands:
        try:
            tokens = shlex.split(command_text)
        except ValueError as error:
            outputs.append(f"拒绝执行：命令格式无效（{error}）。")
            continue
        command_groups: list[list[str]] = [[]]
        for token in tokens:
            if token == "&&":
                command_groups.append([])
            else:
                command_groups[-1].append(token)
        if any(not command for command in command_groups):
            outputs.append("拒绝执行：&& 两侧都必须是完整命令。")
            continue
        for command in command_groups:
            validation_error = validate_read_only_command(
                command, workspace=workspace_path, permissions=permissions
            )
            if validation_error:
                outputs.append(validation_error)
                break
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace_path,
                env={"PATH": os.environ.get("PATH", os.defpath)},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
            outputs.append(output or f"命令已执行，退出码为 {process.returncode}。")
            if process.returncode != 0:
                break
    return "\n".join(outputs)


def build_read_only_workspace_tool(
    *,
    workspace: str | Path | None = None,
    permissions: AgentPermissions = DEFAULT_AGENT_PERMISSIONS,
) -> FunctionTool:
    """构造严格 schema 的 workspace 检查工具。"""
    workspace_path = resolve_workspace(workspace)

    @function_tool(
        name_override="inspect_workspace",
        description_override=(
            "Execute one or more allowlisted, read-only commands inside the "
            "workspace and return their combined output. Each item must be a "
            "direct command without pipes, redirection, or shell expansion."
        ),
        strict_mode=True,
    )
    async def inspect_workspace(commands: list[str]) -> str:
        """检查 workspace 文件和 Git 元数据。

        Args:
            commands: 按顺序执行的直接只读命令。
        """
        return await execute_read_only_shell(
            commands, workspace=workspace_path, permissions=permissions
        )

    return inspect_workspace
