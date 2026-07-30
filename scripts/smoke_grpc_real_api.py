"""使用真实模型 API 验证独立 Hermes gRPC Server 的最小端到端链路。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import signal
import sys
from typing import Final

import grpc

# 载入接口适配器会注册 protoc 生成代码所需的兼容模块别名；测试仍只通过
# gRPC 网络边界访问 server，不会直接实例化 Server 类。
from hermes.interfaces import grpc_server as _grpc_server  # noqa: F401
from hermes.interfaces.generated.v1 import agent_pb2, agent_pb2_grpc


ROOT: Final = Path(__file__).resolve().parents[1]
READY_PATTERN: Final = re.compile(r"已在 127\.0\.0\.1:(\d+) 启动")
START_TIMEOUT_SECONDS: Final = 20
TURN_TIMEOUT_SECONDS: Final = 120


async def wait_for_server(process: asyncio.subprocess.Process) -> int:
    """读取安全的启动日志，返回临时分配的回环端口。"""
    assert process.stderr is not None
    try:
        async with asyncio.timeout(START_TIMEOUT_SECONDS):
            while line := await process.stderr.readline():
                match = READY_PATTERN.search(line.decode("utf-8", errors="replace"))
                if match:
                    return int(match.group(1))
    except TimeoutError as error:
        raise RuntimeError("Hermes gRPC Server 未在限定时间内启动。") from error
    raise RuntimeError("Hermes gRPC Server 在就绪前退出。")


async def stop_server(process: asyncio.subprocess.Process) -> None:
    """结束整个子进程组，避免 ``uv`` 子进程遗留监听器。"""
    process_group = process.pid
    if process.returncode is None:
        os.killpg(process_group, signal.SIGINT)
    try:
        async with asyncio.timeout(10):
            await process.wait()
    except TimeoutError:
        os.killpg(process_group, signal.SIGTERM)
        try:
            async with asyncio.timeout(5):
                await process.wait()
        except TimeoutError:
            os.killpg(process_group, signal.SIGKILL)
            await process.wait()

    # ``uv run`` 可能是父进程，而 server 是其子进程。确认进程组也已退出，
    # 防止脚本已返回但回环端口仍被后台进程占用。
    for _ in range(20):
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.1)
    os.killpg(process_group, signal.SIGTERM)
    await asyncio.sleep(0.1)
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return
    os.killpg(process_group, signal.SIGKILL)


async def run_smoke_test() -> None:
    """启动真实服务、执行 HealthCheck 和一个真实 Turn。"""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("未检测到 OPENAI_API_KEY，不能运行真实 API 冒烟测试。")

    process = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "hermes-grpc-server",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        cwd=ROOT,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
        start_new_session=True,
    )
    channel: grpc.aio.Channel | None = None
    try:
        port = await wait_for_server(process)
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        stub = agent_pb2_grpc.HermesAgentStub(channel)

        health = await stub.HealthCheck(agent_pb2.HealthCheckRequest(), timeout=10)
        if health.status != agent_pb2.SERVING_STATUS_SERVING:
            raise RuntimeError("HealthCheck 未返回 SERVING。")

        session = await stub.CreateSession(agent_pb2.CreateSessionRequest(), timeout=10)
        request = agent_pb2.RunTurnRequest(
            session_id=session.session_id,
            user_input="请只回复：Hermes gRPC real API smoke test passed。",
        )
        events = []
        async with asyncio.timeout(TURN_TIMEOUT_SECONDS):
            async for event in stub.RunTurn(request):
                events.append(event)

        sequences = [event.sequence for event in events]
        if sequences != list(range(1, len(events) + 1)):
            raise RuntimeError("RunTurn 返回的 sequence 不连续。")
        if not events or events[-1].WhichOneof("payload") != "turn_completed":
            raise RuntimeError("真实 Turn 未以 turn_completed 终止。")
        if not events[-1].turn_completed.final_output.strip():
            raise RuntimeError("真实 Turn 未产生最终文本。")
        print(f"真实 API 冒烟测试通过：session={session.session_id}，事件数={len(events)}")
    finally:
        if channel is not None:
            await channel.close()
        await stop_server(process)


def main() -> None:
    try:
        asyncio.run(run_smoke_test())
    except (grpc.RpcError, RuntimeError, TimeoutError) as error:
        print(f"真实 API 冒烟测试失败：{error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
