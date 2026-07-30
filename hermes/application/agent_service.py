"""不依赖 CLI 或 gRPC 的 Agent 应用服务。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from hermes.domain import AgentEvent, AgentEventType, Session, Turn, TurnStatus


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """应用层传给具体 Agent runner 的单轮请求。"""

    text: str
    history: tuple[Any, ...]
    first_turn: bool


class RunnerEventType(StrEnum):
    """基础设施 runner 与应用层之间的内部事件。"""

    CONTENT_DELTA = "content_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class RunnerEvent:
    """基础设施执行结果；SDK 类型不得越过此边界。"""

    type: RunnerEventType
    text: str = ""
    data: dict[str, Any] | None = None
    history: tuple[Any, ...] | None = None


class AgentRunner(Protocol):
    """具体模型/SDK runner 必须实现的端口。"""

    def run(self, request: TurnRequest) -> AsyncIterator[RunnerEvent]:
        """执行一轮请求并返回内部事件流。"""
        ...


class AgentService:
    """管理会话、Turn 和事务式历史提交的应用服务。"""

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner
        self._sessions: dict[str, Session] = {}

    def create_session(self, session_id: str | None = None) -> Session:
        """创建并注册会话。"""
        resolved_id = session_id or uuid4().hex
        if resolved_id in self._sessions:
            raise ValueError(f"session 已存在：{resolved_id}")
        session = Session(id=resolved_id)
        self._sessions[resolved_id] = session
        return session

    def get_session(self, session_id: str) -> Session:
        """返回已有会话。"""
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise KeyError(f"未知 session：{session_id}") from error

    async def run_turn(
        self,
        session_id: str,
        text: str,
    ) -> AsyncIterator[AgentEvent]:
        """执行一轮并产生有序领域事件，成功后才提交历史。"""
        if not text or not text.strip():
            raise ValueError("text 必须是非空的用户输入。")

        session = self.get_session(session_id)
        turn = Turn(id=uuid4().hex, user_text=text.strip())
        session.turns.append(turn)
        sequence = 1
        yield AgentEvent(
            session_id=session.id,
            turn_id=turn.id,
            sequence=sequence,
            type=AgentEventType.TURN_STARTED,
        )

        request = TurnRequest(
            text=turn.user_text,
            history=tuple(session.history),
            first_turn=not session.history,
        )
        completed = False
        try:
            async for runner_event in self._runner.run(request):
                sequence += 1
                if runner_event.type == RunnerEventType.COMPLETED:
                    if runner_event.history is None:
                        raise RuntimeError("runner 完成事件缺少结构化历史。")
                    session.history[:] = runner_event.history
                    turn.status = TurnStatus.COMPLETED
                    turn.final_output = runner_event.text
                    completed = True
                    event_type = AgentEventType.TURN_COMPLETED
                else:
                    event_type = AgentEventType(runner_event.type.value)
                yield AgentEvent(
                    session_id=session.id,
                    turn_id=turn.id,
                    sequence=sequence,
                    type=event_type,
                    text=runner_event.text,
                    data=runner_event.data,
                )

            if not completed:
                raise RuntimeError("runner 事件流未产生完成事件。")
        except asyncio.CancelledError:
            turn.status = TurnStatus.CANCELLED
            sequence += 1
            yield AgentEvent(
                session_id=session.id,
                turn_id=turn.id,
                sequence=sequence,
                type=AgentEventType.TURN_CANCELLED,
            )
        except Exception as error:
            turn.status = TurnStatus.FAILED
            turn.error = str(error)
            sequence += 1
            yield AgentEvent(
                session_id=session.id,
                turn_id=turn.id,
                sequence=sequence,
                type=AgentEventType.TURN_FAILED,
                text=str(error),
            )
