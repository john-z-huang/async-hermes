"""Agent 执行过程中对外稳定的领域事件。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AgentEventType(StrEnum):
    """一轮 Agent 执行可能产生的事件类型。"""

    TURN_STARTED = "turn_started"
    CONTENT_DELTA = "content_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    ARTIFACT_CREATED = "artifact_created"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_CANCELLED = "turn_cancelled"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """与 SDK、CLI 和未来 RPC 表示无关的单个有序事件。"""

    session_id: str
    turn_id: str
    sequence: int
    type: AgentEventType
    text: str = ""
    data: dict[str, Any] | None = None
