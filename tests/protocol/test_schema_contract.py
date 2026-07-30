"""Issue #9 的纯 schema 契约测试，不需要启动 gRPC 服务。"""

from __future__ import annotations

from pathlib import Path
import re


SCHEMA = (
    Path(__file__).parents[2] / "proto" / "hermes" / "v1" / "agent.proto"
)


def schema() -> str:
    return SCHEMA.read_text(encoding="utf-8")


def test_exposes_versioned_service_and_required_rpcs() -> None:
    content = schema()

    assert "package hermes.v1;" in content
    for signature in (
        "rpc CreateSession(CreateSessionRequest) returns (Session);",
        "rpc RunTurn(RunTurnRequest) returns (stream AgentEvent);",
        "rpc CancelTurn(CancelTurnRequest) returns (CancelTurnResponse);",
        "rpc GetSession(GetSessionRequest) returns (SessionSnapshot);",
        "rpc ListSessions(ListSessionsRequest) returns (ListSessionsResponse);",
        "rpc HealthCheck(HealthCheckRequest) returns (HealthCheckResponse);",
    ):
        assert signature in content


def test_agent_event_is_extendable_and_carries_ordering_identity() -> None:
    content = schema()
    event_body = re.search(r"message AgentEvent \{(.*?)\n\}", content, re.DOTALL)

    assert event_body is not None
    for field in (
        "string session_id = 1;",
        "string turn_id = 2;",
        "uint64 sequence = 3;",
        "oneof payload {",
        "TurnStarted turn_started = 10;",
        "TextDelta content_delta = 11;",
        "TextDelta reasoning_delta = 12;",
        "ToolStarted tool_started = 13;",
        "ToolFinished tool_finished = 14;",
        "ArtifactCreated artifact_created = 15;",
        "TurnCompleted turn_completed = 16;",
        "TurnFailed turn_failed = 17;",
        "TurnCancelled turn_cancelled = 18;",
    ):
        assert field in event_body.group(1)


def test_error_contract_is_structured_and_public_schema_hides_sdk_types() -> None:
    content = schema()

    for field in (
        "ErrorCode code = 1;",
        "string message = 2;",
        "bool retryable = 3;",
        "optional string debug_reference = 4;",
    ):
        assert field in content
    forbidden = (
        "openai.",
        "agents.",
        "TResponseInputItem",
        "ResponseTextDeltaEvent",
        "api_key",
    )
    assert all(token.casefold() not in content.casefold() for token in forbidden)
