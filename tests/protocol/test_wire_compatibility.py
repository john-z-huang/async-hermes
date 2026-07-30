"""已发布 v1 字段号的 wire-format 回归测试。"""

from __future__ import annotations

from hermes.interfaces.generated.v1 import agent_pb2


def test_content_delta_v1_wire_fixture_remains_stable() -> None:
    # session_id="s", turn_id="t", sequence=1, content_delta.text="hi"
    fixture = bytes.fromhex("0a017312017418015a040a026869")
    event = agent_pb2.AgentEvent()

    event.ParseFromString(fixture)

    assert event.session_id == "s"
    assert event.turn_id == "t"
    assert event.sequence == 1
    assert event.WhichOneof("payload") == "content_delta"
    assert event.content_delta.text == "hi"
    assert event.SerializeToString() == fixture


def test_future_unknown_fields_do_not_break_v1_reader() -> None:
    # field 100 is intentionally unknown to v1; value is varint 1.
    future_fixture = bytes.fromhex("0a017312017418015a040a026869a00601")
    event = agent_pb2.AgentEvent()

    event.ParseFromString(future_fixture)

    assert event.WhichOneof("payload") == "content_delta"
    assert event.content_delta.text == "hi"


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
