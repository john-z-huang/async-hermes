"""将 ``AgentService`` 暴露为仅限本机访问的 Hermes gRPC 服务。"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
import logging
import signal
import sys
from typing import Final, TextIO
from uuid import uuid4

import grpc

from hermes.application import AgentService
from hermes.config import ConfigError, HermesConfig, default_config_path, load_config
from hermes.domain import AgentEvent, AgentEventType, TurnStatus
from hermes.infrastructure.agents_sdk_runner import (
    AgentsSdkRunner,
    AgentsSdkRunnerConfig,
    DEFAULT_REASON_EFFECT,
    SYSTEM_PROMPT,
    USER_PROMPT,
)

from .generated.v1 import agent_pb2

# protoc 默认生成顶层 ``agent_pb2`` 导入；在接口适配器中注册兼容别名，避免
# 修改受协议生成质量门禁保护的产物。
sys.modules.setdefault("agent_pb2", agent_pb2)
from .generated.v1 import agent_pb2_grpc


LOGGER = logging.getLogger(__name__)
PROTOCOL_VERSION: Final = "v1"
LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(slots=True)
class ActiveTurn:
    """服务端正在执行的 Turn 及其取消句柄。"""

    task: asyncio.Task[object]
    turn_id: str | None = None


def _error(code: int, message: str, *, retryable: bool = False) -> agent_pb2.RpcError:
    return agent_pb2.RpcError(code=code, message=message, retryable=retryable)


def _turn_status(status: TurnStatus) -> int:
    return {
        TurnStatus.RUNNING: agent_pb2.TURN_STATUS_RUNNING,
        TurnStatus.COMPLETED: agent_pb2.TURN_STATUS_COMPLETED,
        TurnStatus.FAILED: agent_pb2.TURN_STATUS_FAILED,
        TurnStatus.CANCELLED: agent_pb2.TURN_STATUS_CANCELLED,
    }[status]


def _safe_failure(error: Exception | str) -> agent_pb2.RpcError:
    """把内部异常归类为稳定、不泄露底层内容的 RPC 错误。"""
    name = error.__class__.__name__.casefold() if isinstance(error, Exception) else ""
    text = str(error).casefold()
    if "tool" in name or "tool" in text:
        return _error(agent_pb2.ERROR_CODE_TOOL_FAILED, "工具执行失败。")
    if any(marker in name or marker in text for marker in ("provider", "rate", "connection", "timeout")):
        return _error(agent_pb2.ERROR_CODE_PROVIDER_UNAVAILABLE, "模型服务暂时不可用。", retryable=True)
    if isinstance(error, ValueError):
        return _error(agent_pb2.ERROR_CODE_INVALID_ARGUMENT, "请求配置无效。")
    return _error(agent_pb2.ERROR_CODE_INTERNAL, "服务内部错误。", retryable=True)


def write_startup_handshake(output: TextIO, address: str) -> None:
    """向父进程输出一条不含日志或配置的启动握手记录。"""
    output.write(
        json.dumps(
            {"type": "hermes-started", "address": address, "protocol_version": PROTOCOL_VERSION},
            separators=(",", ":"),
        )
        + "\n"
    )
    output.flush()


class HermesAgentGrpcServicer(agent_pb2_grpc.HermesAgentServicer):
    """Protobuf 与应用层领域模型之间的薄适配器。"""

    def __init__(self, service: AgentService) -> None:
        self._service = service
        self._accepting_turns = True
        self._active_turns: dict[str, ActiveTurn] = {}

    def stop_accepting_turns(self) -> None:
        """进入关闭状态，并使所有活跃 runner 收到取消。"""
        self._accepting_turns = False
        for active_turn in tuple(self._active_turns.values()):
            self._cancel_if_running(active_turn)

    async def wait_for_active_turns(self) -> None:
        """等待已取消的 handler 清理完成。"""
        tasks = [active_turn.task for active_turn in self._active_turns.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def CreateSession(self, request, context):
        try:
            session_id = request.session_id if request.HasField("session_id") else None
            session = self._service.create_session(session_id)
        except ValueError:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, "session_id 已存在。")
        return agent_pb2.Session(session_id=session.id, status=agent_pb2.SESSION_STATUS_ACTIVE)

    async def RunTurn(self, request, context) -> AsyncIterator[agent_pb2.AgentEvent]:
        if not self._accepting_turns:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "服务正在关闭。")
        if not request.user_input or not request.user_input.strip():
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_input 必须非空。")
        try:
            self._service.get_session(request.session_id)
        except KeyError:
            await context.abort(grpc.StatusCode.NOT_FOUND, "session 不存在。")

        if request.session_id in self._active_turns:
            yield self._failed_event(
                request.session_id,
                agent_pb2.ERROR_CODE_TURN_ALREADY_RUNNING,
                "该 session 已有正在运行的 Turn。",
            )
            return

        task = asyncio.current_task()
        assert task is not None
        active_turn = ActiveTurn(task=task)
        self._active_turns[request.session_id] = active_turn
        context.add_done_callback(lambda _: self._cancel_if_running(active_turn))
        try:
            async for event in self._service.run_turn(request.session_id, request.user_input):
                active_turn.turn_id = event.turn_id
                yield self._to_proto_event(event)
        finally:
            if self._active_turns.get(request.session_id) is active_turn:
                del self._active_turns[request.session_id]

    async def CancelTurn(self, request, context):
        active_turn = self._active_turns.get(request.session_id)
        if active_turn and active_turn.turn_id == request.turn_id:
            self._cancel_if_running(active_turn)
            return agent_pb2.CancelTurnResponse(result=agent_pb2.CANCEL_RESULT_ACCEPTED)

        try:
            session = self._service.get_session(request.session_id)
        except KeyError:
            return agent_pb2.CancelTurnResponse(
                error=_error(agent_pb2.ERROR_CODE_SESSION_NOT_FOUND, "session 不存在。")
            )
        if any(turn.id == request.turn_id for turn in session.turns):
            return agent_pb2.CancelTurnResponse(result=agent_pb2.CANCEL_RESULT_ALREADY_TERMINAL)
        return agent_pb2.CancelTurnResponse(
            error=_error(agent_pb2.ERROR_CODE_TURN_NOT_FOUND, "turn 不存在。")
        )

    async def GetSession(self, request, context):
        try:
            session = self._service.get_session(request.session_id)
        except KeyError:
            await context.abort(grpc.StatusCode.NOT_FOUND, "session 不存在。")
        return self._session_snapshot(session)

    async def ListSessions(self, request, context):
        return agent_pb2.ListSessionsResponse(
            sessions=[self._session_snapshot(session) for session in self._service.list_sessions()]
        )

    async def HealthCheck(self, request, context):
        status = agent_pb2.SERVING_STATUS_SERVING if self._accepting_turns else agent_pb2.SERVING_STATUS_NOT_SERVING
        return agent_pb2.HealthCheckResponse(status=status, protocol_version=PROTOCOL_VERSION)

    @staticmethod
    def _cancel_if_running(active_turn: ActiveTurn) -> None:
        if not active_turn.task.done():
            active_turn.task.cancel()

    @staticmethod
    def _session_snapshot(session) -> agent_pb2.SessionSnapshot:
        return agent_pb2.SessionSnapshot(
            session_id=session.id,
            status=agent_pb2.SESSION_STATUS_ACTIVE,
            turns=[agent_pb2.TurnSummary(turn_id=turn.id, status=_turn_status(turn.status)) for turn in session.turns],
        )

    @staticmethod
    def _failed_event(session_id: str, code: int, message: str) -> agent_pb2.AgentEvent:
        return agent_pb2.AgentEvent(
            session_id=session_id,
            turn_id=uuid4().hex,
            sequence=1,
            turn_failed=agent_pb2.TurnFailed(error=_error(code, message)),
        )

    @staticmethod
    def _to_proto_event(event: AgentEvent) -> agent_pb2.AgentEvent:
        message = agent_pb2.AgentEvent(session_id=event.session_id, turn_id=event.turn_id, sequence=event.sequence)
        data = event.data or {}
        if event.type == AgentEventType.TURN_STARTED:
            message.turn_started.CopyFrom(agent_pb2.TurnStarted())
        elif event.type == AgentEventType.CONTENT_DELTA:
            message.content_delta.text = event.text
        elif event.type == AgentEventType.REASONING_DELTA:
            message.reasoning_delta.text = event.text
        elif event.type == AgentEventType.TOOL_STARTED:
            message.tool_started.tool_name = str(data.get("tool_name", event.text))
            message.tool_started.summary = str(data.get("summary", ""))
        elif event.type == AgentEventType.TOOL_FINISHED:
            message.tool_finished.tool_name = str(data.get("tool_name", event.text))
            message.tool_finished.summary = str(data.get("summary", ""))
            message.tool_finished.status = agent_pb2.TOOL_STATUS_FAILED if data.get("status") == "failed" else agent_pb2.TOOL_STATUS_SUCCEEDED
        elif event.type == AgentEventType.ARTIFACT_CREATED:
            message.artifact_created.artifact_id = str(data.get("artifact_id", ""))
            message.artifact_created.display_name = str(data.get("display_name", ""))
            message.artifact_created.media_type = str(data.get("media_type", ""))
        elif event.type == AgentEventType.TURN_COMPLETED:
            message.turn_completed.final_output = event.text
        elif event.type == AgentEventType.TURN_CANCELLED:
            message.turn_cancelled.CopyFrom(agent_pb2.TurnCancelled())
        elif event.type == AgentEventType.TURN_FAILED:
            message.turn_failed.error.CopyFrom(_safe_failure(event.text))
        else:
            message.turn_failed.error.CopyFrom(_error(agent_pb2.ERROR_CODE_INTERNAL, "未知领域事件。"))
        return message


class HermesGrpcServer:
    """gRPC server 的进程级生命周期管理器。"""

    def __init__(self, service: AgentService, *, host: str = "127.0.0.1", port: int = 0) -> None:
        if host not in LOOPBACK_HOSTS:
            raise ValueError("Hermes gRPC Server 只能监听 loopback 地址。")
        if not 0 <= port <= 65535:
            raise ValueError("port 必须在 0 到 65535 之间。")
        self.host = host
        self.port = port
        self.servicer = HermesAgentGrpcServicer(service)
        self._server = grpc.aio.server()
        agent_pb2_grpc.add_HermesAgentServicer_to_server(self.servicer, self._server)
        self._started = False

    @property
    def address(self) -> str:
        """实际绑定地址；start 前端口为配置值。"""
        return f"{self.host}:{self.port}"

    async def start(self) -> int:
        if self._started:
            return self.port
        bound_port = self._server.add_insecure_port(self.address)
        if not bound_port:
            raise RuntimeError(f"无法绑定 gRPC 地址 {self.address}。")
        self.port = bound_port
        await self._server.start()
        self._started = True
        LOGGER.info("Hermes gRPC Server 已在 %s 启动", self.address)
        return self.port

    async def stop(self, grace: float = 0) -> None:
        """停止接收新请求、取消活跃 Turn 并释放监听端口。"""
        self.servicer.stop_accepting_turns()
        await self.servicer.wait_for_active_turns()
        if self._started:
            await self._server.stop(grace)
            self._started = False
            LOGGER.info("Hermes gRPC Server 已停止")

    async def wait_for_termination(self) -> None:
        await self._server.wait_for_termination()


async def _serve(
    host: str,
    port: int,
    config: HermesConfig | None = None,
    *,
    startup_handshake: bool = False,
) -> None:
    agent = config.agent if config is not None else None
    runner = AgentsSdkRunner(
        AgentsSdkRunnerConfig(
            workspace=agent.workspace if agent and agent.workspace is not None else None,
            permissions=agent.permissions if agent and agent.permissions is not None else "read-only",
            enable_reasoning=agent.enable_reasoning if agent and agent.enable_reasoning is not None else False,
            reason_effect=agent.reason_effect if agent and agent.reason_effect is not None else DEFAULT_REASON_EFFECT,
            system_prompt=agent.system_prompt if agent and agent.system_prompt is not None else SYSTEM_PROMPT,
            user_prompt=agent.user_prompt if agent and agent.user_prompt is not None else USER_PROMPT,
            content=agent.content if agent and agent.content is not None else "",
        )
    )
    server = HermesGrpcServer(AgentService(runner), host=host, port=port)
    await server.start()
    if startup_handshake:
        write_startup_handshake(sys.stdout, server.address)
    shutdown_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for received_signal in handled_signals:
        try:
            loop.add_signal_handler(received_signal, shutdown_requested.set)
        except NotImplementedError:  # pragma: no cover - Windows 不支持 asyncio 信号处理器。
            pass
    try:
        terminated = asyncio.create_task(server.wait_for_termination())
        shutdown = asyncio.create_task(shutdown_requested.wait())
        done, pending = await asyncio.wait((terminated, shutdown), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*done, return_exceptions=True)
    finally:
        for received_signal in handled_signals:
            try:
                loop.remove_signal_handler(received_signal)
            except NotImplementedError:  # pragma: no cover - Windows 不支持 asyncio 信号处理器。
                pass
        await server.stop()


def main(argv: list[str] | None = None) -> None:
    """可独立启动的本地 gRPC server 入口。"""
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config")
    config_args, _ = config_parser.parse_known_args(argv)
    config: HermesConfig | None = None
    config_path = config_args.config or default_config_path()
    if config_path is not None:
        try:
            config = load_config(config_path)
        except ConfigError as error:
            config_parser.error(str(error))
    parser = argparse.ArgumentParser(description="启动 Hermes 本地 gRPC Server")
    parser.add_argument("--config", default=config_path)
    parser.add_argument("--host", default=config.rpc.host if config is not None else "127.0.0.1")
    parser.add_argument("--port", type=int, default=config.rpc.port if config is not None else 0)
    parser.add_argument("--startup-handshake", action="store_true", help="向 stdout 输出机器可读启动信息")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_serve(args.host, args.port, config, startup_handshake=args.startup_handshake))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
