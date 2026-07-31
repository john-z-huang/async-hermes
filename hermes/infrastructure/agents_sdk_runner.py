"""OpenAI Agents SDK 的基础设施适配器。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal, TypeGuard
from urllib.parse import urlparse

from agents import Agent, ModelSettings, Runner, set_tracing_disabled
from openai.types.responses import (
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseReasoningTextDeltaEvent,
    ResponseTextDeltaEvent,
)
from openai.types.shared import Reasoning

from hermes.application import RunnerEvent, RunnerEventType, TurnRequest

from .workspace_tools import (
    AgentPermissions,
    build_read_only_workspace_tool,
    resolve_permissions,
    resolve_workspace,
)

ReasonEffect = Literal["minimal", "low", "medium", "high", "xhigh", "max"]
REASON_EFFECTS: tuple[ReasonEffect, ...] = (
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)
DEFAULT_REASON_EFFECT: ReasonEffect = "medium"

SYSTEM_PROMPT = """\
You are a general-purpose Code Agent. Complete the user's task accurately and
practically. Analyze the requirements before answering, make reasonable
assumptions explicit, and provide code or commands when they are useful.
Prefer simple, maintainable solutions and mention important limitations or
verification steps. Never claim that you ran code, accessed files, or used a
tool unless that capability was actually provided and used.

You may call the inspect_workspace function to inspect the workspace. It is
read-only: use it only to discover files or read their contents, and do not
attempt to modify files, install dependencies, change Git state, or access
paths outside the workspace. Pass one or more direct commands in its commands
array, without pipes, redirections, command chaining, or helper commands such
as printf. Use a structured function call; never print or serialize a tool call
such as <tool_call> in ordinary response text. The allowed commands are: cat,
find, git, head, ls, pwd, rg, tail, and wc.
When you need to distinguish files from directories, use `ls -1p` so directory
names have a trailing slash.
"""

USER_PROMPT = """\
Complete the task in the question below. Use the additional context when it is
relevant. Return the final result together with only the explanation needed to
understand or use it.
"""


def is_reason_effect(value: str) -> TypeGuard[ReasonEffect]:
    """字符串是否是支持的推理强度。"""
    return value in REASON_EFFECTS


def build_task_input(
    question: str,
    user_prompt: str = USER_PROMPT,
    content: str = "",
) -> str:
    """构造首轮用户输入。"""
    if not question or not question.strip():
        raise ValueError("question 是必填参数，必须提供非空的任务内容。")
    sections = [user_prompt.strip(), f"<question>\n{question.strip()}\n</question>"]
    if content and content.strip():
        sections.append(f"<content>\n{content.strip()}\n</content>")
    return "\n\n".join(sections)


@dataclass(frozen=True, slots=True)
class AgentsSdkRunnerConfig:
    """Agents SDK runner 的进程级配置。"""

    system_prompt: str = SYSTEM_PROMPT
    user_prompt: str = USER_PROMPT
    content: str = ""
    model: str | None = None
    enable_reasoning: bool = False
    reason_effect: str = DEFAULT_REASON_EFFECT
    workspace: str | Path | None = None
    permissions: str | AgentPermissions = "read-only"


class AgentsSdkRunner:
    """将 Agents SDK 原始事件转换为应用层内部事件。"""

    def __init__(self, config: AgentsSdkRunnerConfig | None = None) -> None:
        self.config = config or AgentsSdkRunnerConfig()
        self.workspace = resolve_workspace(self.config.workspace)
        self.permissions = resolve_permissions(self.config.permissions)

    def _reasoning(self) -> Reasoning:
        if not self.config.enable_reasoning:
            return Reasoning(effort="none")
        if not is_reason_effect(self.config.reason_effect):
            available = ", ".join(REASON_EFFECTS)
            raise ValueError(
                f"未知 reason-effect 值 {self.config.reason_effect!r}；"
                f"当前可用值：{available}"
            )
        return Reasoning(effort=self.config.reason_effect)

    def run(self, request: TurnRequest) -> AsyncIterator[RunnerEvent]:
        """执行一轮 Agents SDK 请求。"""
        return self._run(request)

    async def _run(self, request: TurnRequest) -> AsyncIterator[RunnerEvent]:
        base_url = os.environ.get("OPENAI_BASE_URL", "")
        if base_url and urlparse(base_url).hostname != "api.openai.com":
            set_tracing_disabled(True)

        if request.first_turn:
            task_input: str | list[object] = build_task_input(
                request.text,
                user_prompt=self.config.user_prompt,
                content=self.config.content,
            )
        else:
            task_input = [
                *request.history,
                {"role": "user", "content": request.text},
            ]

        agent = Agent(
            name="Code Agent",
            instructions=self.config.system_prompt,
            model=self.config.model,
            model_settings=ModelSettings(reasoning=self._reasoning()),
            tools=[
                build_read_only_workspace_tool(
                    workspace=self.workspace,
                    permissions=self.permissions,
                )
            ],
        )
        result = Runner.run_streamed(agent, task_input)
        async for event in result.stream_events():
            if event.type == "run_item_stream_event":
                item = event.item
                if item.type == "tool_call_item":
                    yield RunnerEvent(
                        RunnerEventType.TOOL_STARTED,
                        text=item.title or item.description or "tool",
                        data={"item_type": item.type},
                    )
                elif item.type == "tool_call_output_item":
                    yield RunnerEvent(
                        RunnerEventType.TOOL_FINISHED,
                        text=str(item.output),
                        data={"item_type": item.type},
                    )
                continue
            if event.type != "raw_response_event":
                continue
            data = event.data
            if isinstance(data, ResponseTextDeltaEvent):
                yield RunnerEvent(RunnerEventType.CONTENT_DELTA, text=data.delta)
            elif self.config.enable_reasoning and isinstance(
                data,
                (
                    ResponseReasoningTextDeltaEvent,
                    ResponseReasoningSummaryTextDeltaEvent,
                ),
            ):
                yield RunnerEvent(RunnerEventType.REASONING_DELTA, text=data.delta)

        yield RunnerEvent(
            RunnerEventType.COMPLETED,
            text=str(result.final_output),
            history=tuple(result.to_input_list()),
        )
