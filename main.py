"""A small command-line Code Agent built with the OpenAI Agents SDK."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from agents import Agent, Runner

SYSTEM_PROMPT = """\
You are a general-purpose Code Agent. Complete the user's task accurately and
practically. Analyze the requirements before answering, make reasonable
assumptions explicit, and provide code or commands when they are useful.
Prefer simple, maintainable solutions and mention important limitations or
verification steps. Never claim that you ran code, accessed files, or used a
tool unless that capability was actually provided and used.
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


async def run_code_agent(
    question: str,
    system_prompt: str = SYSTEM_PROMPT,
    user_prompt: str = USER_PROMPT,
    content: str = "",
) -> str:
    """Run the Code Agent once and return its final output."""
    task_input = build_task_input(
        question=question,
        user_prompt=user_prompt,
        content=content,
    )
    agent = Agent(
        name="Code Agent",
        instructions=system_prompt,
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
