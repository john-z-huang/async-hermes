"""协议文档中的可执行示例必须与生成类型一致。"""

from __future__ import annotations

import json
from pathlib import Path
import re

from google.protobuf.json_format import ParseDict

from hermes.interfaces.generated.v1 import agent_pb2


DOCUMENT = Path(__file__).parents[2] / "docs" / "RPC_PROTOCOL.md"


def json_examples() -> list[dict[str, object]]:
    content = DOCUMENT.read_text(encoding="utf-8")
    return [json.loads(example) for example in re.findall(r"```json\n(.*?)\n```", content)]


def test_minimal_request_example_matches_run_turn_request() -> None:
    request = agent_pb2.RunTurnRequest()

    ParseDict(json_examples()[0], request)

    assert request.session_id == "session-1"
    assert request.user_input == "总结当前项目"


def test_streaming_event_example_matches_agent_event() -> None:
    event = agent_pb2.AgentEvent()

    ParseDict(json_examples()[1], event)

    assert event.session_id == "session-1"
    assert event.turn_id == "turn-1"
    assert event.sequence == 2
    assert event.WhichOneof("payload") == "content_delta"
    assert event.content_delta.text == "这是摘要。"
