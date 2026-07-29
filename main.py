"""A small command-line Code Agent built with the OpenAI Agents SDK."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import os
from pathlib import Path
import shlex
from urllib.parse import urlparse

from agents import Agent, Runner, ShellCommandRequest, ShellTool, set_tracing_disabled

PROJECT_ROOT = Path(__file__).resolve().parent
READ_ONLY_COMMANDS = frozenset({"cat", "find", "git", "head", "ls", "pwd", "rg", "tail", "wc"})
READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {"branch", "diff", "grep", "log", "ls-files", "rev-parse", "show", "status"}
)
FORBIDDEN_ARGUMENTS = frozenset(
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
)
FORBIDDEN_ARGUMENT_PREFIXES = ("--config=", "--git-dir=", "--pre=", "--work-tree=")

SYSTEM_PROMPT = """\
You are a general-purpose Code Agent. Complete the user's task accurately and
practically. Analyze the requirements before answering, make reasonable
assumptions explicit, and provide code or commands when they are useful.
Prefer simple, maintainable solutions and mention important limitations or
verification steps. Never claim that you ran code, accessed files, or used a
tool unless that capability was actually provided and used.

You may use the shell to inspect the project. The shell is read-only: use it
only to discover files or read their contents, and do not attempt to modify
files, install dependencies, change Git state, or access paths outside the
project. Invoke only one direct command per shell command, without pipes,
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


def _validate_read_only_command(command: Sequence[str]) -> str | None:
    """Return an error message when a shell command exceeds read-only access."""
    if not command:
        return "拒绝执行：未提供命令。"

    executable, *arguments = command
    if executable not in READ_ONLY_COMMANDS:
        return f"拒绝执行：{executable!r} 不在只读命令白名单中。"
    if any(argument in FORBIDDEN_ARGUMENTS for argument in arguments):
        return "拒绝执行：命令包含可能修改文件或执行其他命令的参数。"
    if any(argument.startswith(FORBIDDEN_ARGUMENT_PREFIXES) for argument in arguments):
        return "拒绝执行：命令包含不允许的配置或执行参数。"
    for argument in arguments:
        if argument == ".." or argument.startswith("../"):
            return "拒绝执行：只允许访问项目目录内的路径。"
        if argument.startswith("/") and not Path(argument).resolve().is_relative_to(PROJECT_ROOT):
            return "拒绝执行：只允许访问项目目录内的路径。"
    if executable == "git":
        subcommand = next((argument for argument in arguments if not argument.startswith("-")), None)
        if subcommand not in READ_ONLY_GIT_SUBCOMMANDS:
            return "拒绝执行：只允许只读 Git 子命令。"
    return None


async def execute_read_only_shell(request: ShellCommandRequest) -> str:
    """Execute allowlisted read-only shell commands inside the project root."""
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
            validation_error = _validate_read_only_command(command)
            if validation_error:
                outputs.append(validation_error)
                break

            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=PROJECT_ROOT,
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
) -> str:
    """Run the Code Agent once and return its final output."""
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
        tools=[ShellTool(executor=execute_read_only_shell)],
    )
    result = await Runner.run(agent, task_input)
    return str(result.final_output)


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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command-line application."""
    args = parse_args(argv)
    output = asyncio.run(
        run_code_agent(
            question=args.question,
            system_prompt=args.system_prompt,
            user_prompt=args.user_prompt,
            content=args.content,
        )
    )
    print(output)


if __name__ == "__main__":
    main()
