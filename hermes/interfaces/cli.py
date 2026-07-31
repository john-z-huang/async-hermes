"""Hermes 命令行适配器。"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import TextIO

from hermes.application import AgentService
from hermes.config import ConfigError, HermesConfig, default_config_path, load_config
from hermes.domain import AgentEvent, AgentEventType
from hermes.infrastructure.agents_sdk_runner import (
    AgentsSdkRunner,
    AgentsSdkRunnerConfig,
    DEFAULT_REASON_EFFECT,
    REASON_EFFECTS,
    SYSTEM_PROMPT,
    USER_PROMPT,
)
from hermes.infrastructure.persistence import persist_agent_output
from hermes.infrastructure.workspace_tools import (
    PERMISSION_PROFILES,
    resolve_workspace,
)


@dataclass
class EventRenderer:
    """将结构化领域事件渲染为当前 CLI 格式。"""

    output_stream: TextIO | None
    capture_stream: TextIO | None
    enable_reasoning: bool
    active_section: str | None = None
    wrote_output: bool = False
    last_chunk_ends_with_newline: bool = False

    def _streams(self) -> list[TextIO]:
        streams = [
            stream
            for stream in (self.output_stream, self.capture_stream)
            if stream is not None
        ]
        if len(streams) == 2 and streams[0] is streams[1]:
            streams.pop()
        return streams

    def _write(self, text: str) -> None:
        for stream in self._streams():
            stream.write(text)
            stream.flush()

    def render(self, event: AgentEvent) -> None:
        """渲染内容或推理增量，忽略生命周期事件。"""
        if event.type == AgentEventType.CONTENT_DELTA:
            section = "content"
        elif event.type == AgentEventType.REASONING_DELTA:
            if not self.enable_reasoning:
                return
            section = "reasoning"
        else:
            return
        if not event.text or not self._streams():
            return
        if self.enable_reasoning and section != self.active_section:
            if self.wrote_output and not self.last_chunk_ends_with_newline:
                self._write("\n")
            self._write(f"[{section}]\n")
            self.active_section = section
        self._write(event.text)
        self.wrote_output = True
        self.last_chunk_ends_with_newline = event.text.endswith("\n")

    def finish(self) -> None:
        """保证已渲染内容以换行结束。"""
        if self._streams() and self.wrote_output and not self.last_chunk_ends_with_newline:
            self._write("\n")


async def consume_turn(
    events: AsyncIterator[AgentEvent],
    renderer: EventRenderer,
) -> str:
    """消费一轮领域事件并返回最终文本；失败作为异常交给 CLI。"""
    final_output: str | None = None
    async for event in events:
        renderer.render(event)
        if event.type == AgentEventType.TURN_COMPLETED:
            final_output = event.text
        elif event.type == AgentEventType.TURN_FAILED:
            raise RuntimeError(event.text)
        elif event.type == AgentEventType.TURN_CANCELLED:
            raise asyncio.CancelledError
    renderer.finish()
    if final_output is None:
        raise RuntimeError("Turn 未产生完成事件。")
    return final_output


def _parse_boolean(value: str) -> bool:
    normalized = value.casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("必须使用 true 或 false。")


def _workspace_argument(value: str) -> Path:
    try:
        return resolve_workspace(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", type=Path)
    config_args, _ = config_parser.parse_known_args(argv)
    config: HermesConfig | None = None
    config_path = config_args.config or default_config_path()
    if config_path is not None:
        try:
            config = load_config(
                config_path,
                defaults=default_config_path() if config_args.config is not None else None,
            )
        except ConfigError as error:
            config_parser.error(str(error))
    agent_config = config.agent if config is not None else None
    parser = argparse.ArgumentParser(
        description="Run a simple Code Agent with the OpenAI Agents SDK."
    )
    parser.add_argument("--config", type=Path, default=config_path)
    parser.add_argument("--question", default=None)
    parser.add_argument(
        "--running-mode", choices=("loop", "one-shot"), default="loop"
    )
    parser.add_argument("--system-prompt", default=agent_config.system_prompt if agent_config and agent_config.system_prompt is not None else SYSTEM_PROMPT)
    parser.add_argument("--user-prompt", default=agent_config.user_prompt if agent_config and agent_config.user_prompt is not None else USER_PROMPT)
    parser.add_argument("--content", default=agent_config.content if agent_config and agent_config.content is not None else "")
    parser.add_argument(
        "--default-model",
        default=agent_config.model if agent_config and agent_config.model is not None else None,
    )
    parser.add_argument(
        "--enable-stream-output",
        nargs="?", const=True, type=_parse_boolean, default=True,
    )
    parser.add_argument(
        "--enable-reasoning",
        nargs="?", const=True, type=_parse_boolean,
        default=agent_config.enable_reasoning if agent_config and agent_config.enable_reasoning is not None else False,
    )
    parser.add_argument("--reason-effect", default=agent_config.reason_effect if agent_config and agent_config.reason_effect is not None else DEFAULT_REASON_EFFECT)
    parser.add_argument(
        "--workspace",
        type=_workspace_argument,
        default=agent_config.workspace if agent_config and agent_config.workspace is not None else Path.cwd().resolve(),
    )
    parser.add_argument("--output-file", default=None)
    parser.add_argument(
        "--permissions",
        choices=tuple(PERMISSION_PROFILES),
        default=agent_config.permissions if agent_config and agent_config.permissions is not None else "read-only",
    )
    args = parser.parse_args(argv)
    if args.running_mode == "one-shot" and (
        args.question is None or not args.question.strip()
    ):
        parser.error("--running-mode one-shot 要求提供非空的 --question")
    if args.enable_reasoning and args.reason_effect not in REASON_EFFECTS:
        parser.error(f"--reason-effect 必须是以下值之一：{', '.join(REASON_EFFECTS)}")
    return args


def build_service(args: argparse.Namespace) -> AgentService:
    """根据 CLI 配置装配应用服务。"""
    runner = AgentsSdkRunner(
        AgentsSdkRunnerConfig(
            system_prompt=args.system_prompt,
            user_prompt=args.user_prompt,
            content=args.content,
            model=args.default_model,
            enable_reasoning=args.enable_reasoning,
            reason_effect=args.reason_effect,
            workspace=args.workspace,
            permissions=args.permissions,
        )
    )
    return AgentService(runner)


def _run_and_persist(
    args: argparse.Namespace,
    question: str,
    *,
    service: AgentService,
    session_id: str,
) -> None:
    """通过同一个 Application Service 执行并保存一轮。"""
    from io import StringIO

    captured_response = StringIO()
    renderer = EventRenderer(
        output_stream=sys.stdout if args.enable_stream_output else None,
        capture_stream=captured_response,
        enable_reasoning=args.enable_reasoning,
    )
    output = asyncio.run(
        consume_turn(service.run_turn(session_id, question), renderer)
    )
    persisted_output = captured_response.getvalue() or output
    output_path = persist_agent_output(
        persisted_output,
        workspace=args.workspace,
        output_file=args.output_file,
    )
    if not args.enable_stream_output:
        print(output)
    print(f"响应结果已保存到：{output_path}")


def _run_loop(
    args: argparse.Namespace,
    *,
    service: AgentService,
    session_id: str,
) -> None:
    pending_question = args.question
    while True:
        if pending_question is not None:
            question = pending_question
            pending_question = None
        else:
            try:
                question = input("question> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return
        normalized_question = question.strip()
        if normalized_question.casefold() in {"/exit", "/quit"}:
            return
        if not normalized_question:
            continue
        try:
            _run_and_persist(
                args,
                normalized_question,
                service=service,
                session_id=session_id,
            )
        except Exception as error:
            print(f"本轮请求失败：{error}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> None:
    """运行命令行应用。"""
    args = parse_args(argv)
    service = build_service(args)
    session = service.create_session()
    if args.running_mode == "one-shot":
        _run_and_persist(
            args, args.question, service=service, session_id=session.id
        )
    else:
        _run_loop(args, service=service, session_id=session.id)
