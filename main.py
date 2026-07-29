"""A small command-line Code Agent built with the OpenAI Agents SDK."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
import os
from pathlib import Path
import shlex
from urllib.parse import urlparse

from agents import (
    Agent,
    ModelSettings,
    Runner,
    ShellCommandRequest,
    ShellTool,
    set_tracing_disabled,
)
from openai.types.shared import Reasoning


@dataclass(frozen=True)
class AgentPermissions:
    """Shell permissions available to an agent inside its workspace."""

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
            "--exec",
            "--execdir",
            "--in-place",
            "--ok",
            "--okdir",
            "--pre",
            "--git-dir",
            "--work-tree",
            "--write",
            "&&",
            "&",
            "-delete",
            "-exec",
            "-execdir",
            "-i",
            "-ok",
            "-okdir",
            ";",
            "|",
            "||",
        }
    ),
    forbidden_argument_prefixes=(
        "--config=",
        "--git-dir=",
        "--pre=",
        "--work-tree=",
    ),
)
PERMISSION_PROFILES = {"read-only": DEFAULT_AGENT_PERMISSIONS}
REASON_EFFECTS = ("minimal", "low", "medium", "high", "xhigh", "max")
DEFAULT_REASON_EFFECT = "medium"

SYSTEM_PROMPT = """\
You are a general-purpose Code Agent. Complete the user's task accurately and
practically. Analyze the requirements before answering, make reasonable
assumptions explicit, and provide code or commands when they are useful.
Prefer simple, maintainable solutions and mention important limitations or
verification steps. Never claim that you ran code, accessed files, or used a
tool unless that capability was actually provided and used.

You may use the shell to inspect the workspace. The shell is read-only: use it
only to discover files or read their contents, and do not attempt to modify
files, install dependencies, change Git state, or access paths outside the
workspace. Invoke only one direct command per shell command, without pipes,
redirections, command chaining, or helper commands such as printf. The allowed
commands are: cat, find, git, head, ls, pwd, rg, tail, and wc.
When you need to distinguish files from directories, use `ls -1p` so directory
names have a trailing slash.
"""

USER_PROMPT = """\
Complete the task in the question below. Use the additional context when it is
relevant. Return the final result together with only the explanation needed to
understand or use it.
"""


def build_task_input(
    question: str,
    user_prompt: str = USER_PROMPT,
    content: str = "",
) -> str:
    """Build the user input sent to the Code Agent."""
    if not question or not question.strip():
        raise ValueError("question 是必填参数，必须提供非空的任务内容。")

    sections = [
        user_prompt.strip(),
        f"<question>\n{question.strip()}\n</question>",
    ]
    if content and content.strip():
        sections.append(f"<content>\n{content.strip()}\n</content>")
    return "\n\n".join(sections)


def _resolve_workspace(workspace: str | Path | None) -> Path:
    """Return an existing workspace directory as an absolute path."""
    resolved = (
        Path.cwd().resolve()
        if workspace is None
        else Path(workspace).expanduser().resolve()
    )
    if not resolved.is_dir():
        raise ValueError(f"workspace 必须是已存在的目录：{resolved}")
    return resolved


def _resolve_permissions(
    permissions: str | AgentPermissions,
) -> AgentPermissions:
    """Resolve a named permission profile or an explicit permission object."""
    if isinstance(permissions, AgentPermissions):
        return permissions
    try:
        return PERMISSION_PROFILES[permissions]
    except KeyError as error:
        available = ", ".join(PERMISSION_PROFILES)
        raise ValueError(
            f"未知 permissions 配置 {permissions!r}；当前可用值：{available}"
        ) from error


def _validate_read_only_command(
    command: Sequence[str],
    workspace: str | Path | None = None,
    permissions: AgentPermissions = DEFAULT_AGENT_PERMISSIONS,
) -> str | None:
    """Return an error message when a shell command exceeds read-only access."""
    if not command:
        return "拒绝执行：未提供命令。"

    workspace_path = _resolve_workspace(workspace)
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
    request: ShellCommandRequest,
    *,
    workspace: str | Path | None = None,
    permissions: AgentPermissions = DEFAULT_AGENT_PERMISSIONS,
) -> str:
    """Execute allowlisted read-only shell commands inside the workspace."""
    workspace_path = _resolve_workspace(workspace)
    outputs: list[str] = []
    for command_text in request.data.action.commands:
        try:
            tokens = shlex.split(command_text)
        except ValueError as error:
            outputs.append(f"拒绝执行：命令格式无效（{error}）。")
            continue

        # Modern shell calls commonly join simple inspection commands with
        # ``&&``. Split and execute them ourselves instead of invoking a shell,
        # so shell expansion, redirection, and command substitution stay off.
        commands: list[list[str]] = [[]]
        for token in tokens:
            if token == "&&":
                commands.append([])
            else:
                commands[-1].append(token)
        if any(not command for command in commands):
            outputs.append("拒绝执行：&& 两侧都必须是完整命令。")
            continue

        for command in commands:
            validation_error = _validate_read_only_command(
                command,
                workspace=workspace_path,
                permissions=permissions,
            )
            if validation_error:
                outputs.append(validation_error)
                break

            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace_path,
                # Preserve only PATH so Homebrew-provided read tools (such as rg) remain
                # available without exposing credentials or other process environment.
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


async def run_code_agent(
    question: str,
    system_prompt: str = SYSTEM_PROMPT,
    user_prompt: str = USER_PROMPT,
    content: str = "",
    enable_reasoning: bool = False,
    reason_effect: str = DEFAULT_REASON_EFFECT,
    workspace: str | Path | None = None,
    permissions: str | AgentPermissions = "read-only",
) -> str:
    """Run the Code Agent once and return its final output."""
    workspace_path = _resolve_workspace(workspace)
    permission_profile = _resolve_permissions(permissions)
    if enable_reasoning and reason_effect not in REASON_EFFECTS:
        available = ", ".join(REASON_EFFECTS)
        raise ValueError(
            f"未知 reason-effect 值 {reason_effect!r}；当前可用值：{available}"
        )

    # The Agents SDK exports traces to OpenAI separately from model requests.
    # A key for an OpenAI-compatible provider cannot authenticate that export.
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if base_url and urlparse(base_url).hostname != "api.openai.com":
        set_tracing_disabled(True)

    task_input = build_task_input(
        question=question,
        user_prompt=user_prompt,
        content=content,
    )
    agent = Agent(
        name="Code Agent",
        instructions=system_prompt,
        model_settings=ModelSettings(
            reasoning=Reasoning(effort=reason_effect)
            if enable_reasoning
            else Reasoning(effort="none")
        ),
        tools=[
            ShellTool(
                executor=partial(
                    execute_read_only_shell,
                    workspace=workspace_path,
                    permissions=permission_profile,
                )
            )
        ],
    )
    result = await Runner.run(agent, task_input)
    return str(result.final_output)


def _parse_boolean(value: str) -> bool:
    """Parse a human-friendly command-line boolean value."""
    normalized = value.casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("必须使用 true 或 false。")


def _workspace_argument(value: str) -> Path:
    """Parse and validate the command-line workspace."""
    try:
        return _resolve_workspace(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run a simple Code Agent with the OpenAI Agents SDK.",
    )
    parser.add_argument(
        "--question",
        required=True,
        help="要交给 Code Agent 执行的任务（必填）。",
    )
    parser.add_argument(
        "--system-prompt",
        default=SYSTEM_PROMPT,
        help="自定义系统提示词；省略时使用内置 SYSTEM_PROMPT。",
    )
    parser.add_argument(
        "--user-prompt",
        default=USER_PROMPT,
        help="自定义任务提示词；省略时使用内置 USER_PROMPT。",
    )
    parser.add_argument(
        "--content",
        default="",
        help="可选的补充信息、上一步结果或中间信息。",
    )
    parser.add_argument(
        "--enable-reasoning",
        nargs="?",
        const=True,
        type=_parse_boolean,
        default=False,
        help="是否启用 Agent 推理模式，可传 true/false（默认 false）。",
    )
    parser.add_argument(
        "--reason-effect",
        default=DEFAULT_REASON_EFFECT,
        help=(
            "推理强度，仅在 enable-reasoning=true 时生效；"
            f"可用值：{', '.join(REASON_EFFECTS)}（默认 {DEFAULT_REASON_EFFECT}）。"
        ),
    )
    parser.add_argument(
        "--workspace",
        type=_workspace_argument,
        default=Path.cwd().resolve(),
        help="Agent 工作目录；省略时使用启动脚本时的当前目录。",
    )
    parser.add_argument(
        "--permissions",
        choices=tuple(PERMISSION_PROFILES),
        default="read-only",
        help="Agent 权限配置；当前仅支持 read-only（默认）。",
    )
    args = parser.parse_args(argv)
    if args.enable_reasoning and args.reason_effect not in REASON_EFFECTS:
        parser.error(
            f"--reason-effect 必须是以下值之一：{', '.join(REASON_EFFECTS)}"
        )
    return args


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command-line application."""
    args = parse_args(argv)
    output = asyncio.run(
        run_code_agent(
            question=args.question,
            system_prompt=args.system_prompt,
            user_prompt=args.user_prompt,
            content=args.content,
            enable_reasoning=args.enable_reasoning,
            reason_effect=args.reason_effect,
            workspace=args.workspace,
            permissions=args.permissions,
        )
    )
    print(output)


if __name__ == "__main__":
    main()
