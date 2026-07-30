"""由 Python 服务层维护的权威会话状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .turn import Turn


@dataclass(slots=True)
class Session:
    """一个进程内会话及其完整结构化 SDK 历史。"""

    id: str
    history: list[Any] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
