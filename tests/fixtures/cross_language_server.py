"""供 Node 跨语言测试启动的确定性 Hermes gRPC Server。"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

from hermes.application import AgentService, RunnerEvent, RunnerEventType, TurnRequest
from hermes.interfaces.grpc_server import HermesGrpcServer, write_startup_handshake


class ScriptedRunner:
    """不访问模型、凭据或网络的测试 runner。"""

    async def run(self, request: TurnRequest):
        if request.text == "failure":
            raise RuntimeError("provider unavailable secret=must-not-cross-wire")
        if request.text == "cancel":
            await asyncio.Event().wait()
            return
        if request.text == "crash":
            os._exit(23)

        yield RunnerEvent(RunnerEventType.CONTENT_DELTA, "an")
        yield RunnerEvent(RunnerEventType.CONTENT_DELTA, "swer")
        yield RunnerEvent(RunnerEventType.REASONING_DELTA, "reason")
        yield RunnerEvent(
            RunnerEventType.TOOL_STARTED,
            data={"tool_name": "inspect_workspace", "summary": "读取文件"},
        )
        yield RunnerEvent(
            RunnerEventType.TOOL_FINISHED,
            data={
                "tool_name": "inspect_workspace",
                "summary": "读取完成",
                "status": "succeeded",
            },
        )
        yield RunnerEvent(
            RunnerEventType.ARTIFACT_CREATED,
            data={
                "artifact_id": "artifact-1",
                "display_name": "报告",
                "media_type": "text/markdown",
            },
        )
        yield RunnerEvent(
            RunnerEventType.COMPLETED,
            "answer",
            history=(*request.history, request.text, "answer"),
        )


async def serve() -> None:
    server = HermesGrpcServer(AgentService(ScriptedRunner()))
    await server.start()
    write_startup_handshake(sys.stdout, server.address)

    shutdown_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    for received_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(received_signal, shutdown_requested.set)
        except NotImplementedError:
            pass
    try:
        await shutdown_requested.wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(serve())
