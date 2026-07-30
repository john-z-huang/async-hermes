"""已发布 v1 字段号的 wire-format 回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes.interfaces.generated.v1 import agent_pb2


FIXTURES = json.loads(
    (
        Path(__file__).parents[1]
        / "fixtures"
        / "protocol"
        / "v1"
        / "agent_events.json"
    ).read_text(encoding="utf-8")
)


def fixture(name: str) -> dict[str, object]:
    return next(item for item in FIXTURES if item["name"] == name)


def parse(item: dict[str, object]) -> agent_pb2.AgentEvent:
    event = agent_pb2.AgentEvent()
    event.ParseFromString(bytes.fromhex(str(item["wire_hex"])))
    return event


@pytest.mark.parametrize(
    "item",
    FIXTURES,
    ids=[str(item["name"]) for item in FIXTURES],
)
def test_shared_wire_fixtures_are_readable_by_python(
    item: dict[str, object],
) -> None:
    event = parse(item)

    assert event.session_id == item["session_id"]
    assert event.turn_id == item["turn_id"]
    assert event.sequence == item["sequence"]
    assert event.WhichOneof("payload") == item["payload"]
    if item["payload"] == "content_delta":
        assert event.content_delta.text == item["text"]


def test_content_delta_v1_wire_fixture_remains_stable() -> None:
    item = fixture("content_delta_v1")
    event = parse(item)

    assert event.SerializeToString() == bytes.fromhex(str(item["wire_hex"]))


def test_published_event_payload_field_numbers_are_not_reassigned() -> None:
    fields = agent_pb2.AgentEvent.DESCRIPTOR.fields_by_name

    assert {
        name: fields[name].number
        for name in (
            "turn_started",
            "content_delta",
            "reasoning_delta",
            "tool_started",
            "tool_finished",
            "artifact_created",
            "turn_completed",
            "turn_failed",
            "turn_cancelled",
        )
    } == {
        "turn_started": 10,
        "content_delta": 11,
        "reasoning_delta": 12,
        "tool_started": 13,
        "tool_finished": 14,
        "artifact_created": 15,
        "turn_completed": 16,
        "turn_failed": 17,
        "turn_cancelled": 18,
    }
