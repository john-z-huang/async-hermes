"""会话中的单轮执行模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TurnStatus(StrEnum):
    """Turn 的生命周期状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class Turn:
    """一次用户输入及其最终状态。"""

    id: str
    user_text: str
    status: TurnStatus = TurnStatus.RUNNING
    final_output: str | None = None
    error: str | None = None
