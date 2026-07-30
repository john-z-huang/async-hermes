"""Hermes gRPC server 的端到端接口测试。"""

from __future__ import annotations

import asyncio
import io
import json

import grpc
import pytest

from hermes.application import AgentService, RunnerEvent, RunnerEventType, TurnRequest
from hermes.interfaces.grpc_server import HermesGrpcServer, write_startup_handshake
from hermes.interfaces.generated.v1 import agent_pb2, agent_pb2_grpc


class FakeRunner:
    def __init__(self, events: list[RunnerEvent] | None = None, error: Exception | None = None) -> None:
        self.events = events or []
        self.error = error
        self.requests: list[TurnRequest] = []

    async def run(self, request: TurnRequest):
        self.requests.append(request)
        if self.error:
            raise self.error
        for event in self.events:
            yield event


class BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self, request: TurnRequest):
        self.started.set()
        try:
            await self.release.wait()
            yield RunnerEvent(RunnerEventType.COMPLETED, "done", history=(request.text,))
        finally:
            self.cancelled.set()


class GrpcHarness:
    def __init__(self, runner) -> None:
        self.service = AgentService(runner)
        self.server = HermesGrpcServer(self.service)
        self.channel: grpc.aio.Channel | None = None
        self.stub = None

    async def start(self) -> None:
        await self.server.start()
        self.channel = grpc.aio.insecure_channel(self.server.address)
        self.stub = agent_pb2_grpc.HermesAgentStub(self.channel)

    async def close(self) -> None:
        if self.channel:
            await self.channel.close()
        await self.server.stop()


async def collect(call) -> list[agent_pb2.AgentEvent]:
    return [event async for event in call]


async def collect_after_read(call) -> list[agent_pb2.AgentEvent]:
    """继续读取已经用 ``read`` 消费过首个事件的流。"""
    events = []
    while True:
        event = await call.read()
        if event is grpc.aio.EOF:
            return events
        events.append(event)


def async_test(test):
    """在不引入 pytest-asyncio 依赖的情况下运行异步集成测试。"""
    def wrapped():
        asyncio.run(test())

    return wrapped


@async_test
async def test_all_rpcs_and_successful_ordered_stream() -> None:
    harness = GrpcHarness(FakeRunner([
        RunnerEvent(RunnerEventType.CONTENT_DELTA, "answer"),
        RunnerEvent(RunnerEventType.COMPLETED, "answer", history=("history",)),
    ]))
    await harness.start()
    try:
        created = await harness.stub.CreateSession(agent_pb2.CreateSessionRequest(session_id="one"))
        events = await collect(harness.stub.RunTurn(agent_pb2.RunTurnRequest(session_id=created.session_id, user_input=" question ")))

        assert [event.sequence for event in events] == [1, 2, 3]
        assert [event.WhichOneof("payload") for event in events] == ["turn_started", "content_delta", "turn_completed"]
        assert len({event.turn_id for event in events}) == 1
        assert events[-1].turn_completed.final_output == "answer"
        snapshot = await harness.stub.GetSession(agent_pb2.GetSessionRequest(session_id="one"))
        assert [(turn.turn_id, turn.status) for turn in snapshot.turns] == [(events[0].turn_id, agent_pb2.TURN_STATUS_COMPLETED)]
        sessions = await harness.stub.ListSessions(agent_pb2.ListSessionsRequest())
        assert [session.session_id for session in sessions.sessions] == ["one"]
        health = await harness.stub.HealthCheck(agent_pb2.HealthCheckRequest())
        assert (health.status, health.protocol_version) == (agent_pb2.SERVING_STATUS_SERVING, "v1")
    finally:
        await harness.close()


@async_test
async def test_input_and_unknown_session_are_transport_errors() -> None:
    harness = GrpcHarness(FakeRunner())
    await harness.start()
    try:
        with pytest.raises(grpc.aio.AioRpcError) as invalid:
            await collect(harness.stub.RunTurn(agent_pb2.RunTurnRequest(session_id="missing", user_input="  ")))
        assert invalid.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        with pytest.raises(grpc.aio.AioRpcError) as missing:
            await harness.stub.GetSession(agent_pb2.GetSessionRequest(session_id="missing"))
        assert missing.value.code() == grpc.StatusCode.NOT_FOUND
    finally:
        await harness.close()


@async_test
async def test_failure_is_safe_and_does_not_commit_history() -> None:
    harness = GrpcHarness(FakeRunner(error=RuntimeError("provider secret=never-send")))
    await harness.start()
    try:
        await harness.stub.CreateSession(agent_pb2.CreateSessionRequest(session_id="one"))
        events = await collect(harness.stub.RunTurn(agent_pb2.RunTurnRequest(session_id="one", user_input="question")))
        failure = events[-1].turn_failed.error
        assert failure.code == agent_pb2.ERROR_CODE_PROVIDER_UNAVAILABLE
        assert "secret" not in failure.message
        snapshot = await harness.stub.GetSession(agent_pb2.GetSessionRequest(session_id="one"))
        assert snapshot.turns[0].status == agent_pb2.TURN_STATUS_FAILED
        assert harness.service.get_session("one").history == []
    finally:
        await harness.close()


@async_test
async def test_cancel_is_idempotent_and_preserves_history() -> None:
    runner = BlockingRunner()
    harness = GrpcHarness(runner)
    await harness.start()
    try:
        await harness.stub.CreateSession(agent_pb2.CreateSessionRequest(session_id="one"))
        call = harness.stub.RunTurn(agent_pb2.RunTurnRequest(session_id="one", user_input="question"))
        started = await call.read()
        assert started.WhichOneof("payload") == "turn_started"
        accepted = await harness.stub.CancelTurn(agent_pb2.CancelTurnRequest(session_id="one", turn_id=started.turn_id))
        assert accepted.result == agent_pb2.CANCEL_RESULT_ACCEPTED
        events = [started, *await collect_after_read(call)]
        assert events[-1].WhichOneof("payload") == "turn_cancelled"
        terminal = await harness.stub.CancelTurn(agent_pb2.CancelTurnRequest(session_id="one", turn_id=started.turn_id))
        assert terminal.result == agent_pb2.CANCEL_RESULT_ALREADY_TERMINAL
        assert harness.service.get_session("one").history == []
        assert runner.cancelled.is_set()
    finally:
        await harness.close()


@async_test
async def test_same_session_rejected_and_sessions_run_independently() -> None:
    runner = BlockingRunner()
    harness = GrpcHarness(runner)
    await harness.start()
    try:
        for session_id in ("one", "two"):
            await harness.stub.CreateSession(agent_pb2.CreateSessionRequest(session_id=session_id))
        first = harness.stub.RunTurn(agent_pb2.RunTurnRequest(session_id="one", user_input="first"))
        first_started = await first.read()
        rejected = await collect(harness.stub.RunTurn(agent_pb2.RunTurnRequest(session_id="one", user_input="second")))
        assert rejected[0].turn_failed.error.code == agent_pb2.ERROR_CODE_TURN_ALREADY_RUNNING
        second = harness.stub.RunTurn(agent_pb2.RunTurnRequest(session_id="two", user_input="other"))
        second_started = await second.read()
        assert {first_started.session_id, second_started.session_id} == {"one", "two"}
        for session_id, turn_id in (("one", first_started.turn_id), ("two", second_started.turn_id)):
            await harness.stub.CancelTurn(agent_pb2.CancelTurnRequest(session_id=session_id, turn_id=turn_id))
        await collect_after_read(first)
        await collect_after_read(second)
    finally:
        await harness.close()


@async_test
async def test_client_disconnect_and_graceful_stop_cancel_runner() -> None:
    runner = BlockingRunner()
    harness = GrpcHarness(runner)
    await harness.start()
    try:
        await harness.stub.CreateSession(agent_pb2.CreateSessionRequest(session_id="one"))
        call = harness.stub.RunTurn(agent_pb2.RunTurnRequest(session_id="one", user_input="question"))
        await call.read()
        call.cancel()
        await asyncio.wait_for(runner.cancelled.wait(), timeout=1)
        assert harness.service.get_session("one").history == []
        health = await harness.stub.HealthCheck(agent_pb2.HealthCheckRequest())
        assert health.status == agent_pb2.SERVING_STATUS_SERVING
    finally:
        await harness.close()


@async_test
async def test_graceful_stop_cancels_active_turn_and_cleans_registry() -> None:
    runner = BlockingRunner()
    harness = GrpcHarness(runner)
    await harness.start()
    try:
        await harness.stub.CreateSession(agent_pb2.CreateSessionRequest(session_id="one"))
        call = harness.stub.RunTurn(agent_pb2.RunTurnRequest(session_id="one", user_input="question"))
        await call.read()
        await harness.server.stop()
        assert runner.cancelled.is_set()
        assert harness.server.servicer._active_turns == {}  # noqa: SLF001
        assert harness.service.get_session("one").history == []
    finally:
        await harness.close()


def test_server_rejects_non_loopback_addresses() -> None:
    with pytest.raises(ValueError, match="loopback"):
        HermesGrpcServer(AgentService(FakeRunner()), host="0.0.0.0")


def test_startup_handshake_is_machine_readable_and_contains_no_configuration() -> None:
    output = io.StringIO()

    write_startup_handshake(output, "127.0.0.1:54321")

    assert json.loads(output.getvalue()) == {
        "address": "127.0.0.1:54321",
        "protocol_version": "v1",
        "type": "hermes-started",
    }
