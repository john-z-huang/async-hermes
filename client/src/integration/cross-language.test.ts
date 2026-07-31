import { spawn as nodeSpawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";

import { afterEach, describe, expect, it } from "vitest";

import { ErrorCode } from "../generated/v1/agent.js";
import { PythonServerLifecycle } from "../lifecycle/python-server.js";
import { GrpcHermesClient, RpcConnectionError, type HermesRpcClient } from "../rpc/hermes-client.js";

async function collect<T>(events: AsyncIterable<T>): Promise<T[]> {
  const received: T[] = [];
  for await (const event of events) received.push(event);
  return received;
}

async function expectPortReleased(address: string): Promise<void> {
  const separator = address.lastIndexOf(":");
  const host = address.slice(0, separator);
  const port = Number(address.slice(separator + 1));
  const server = createServer();
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  });
}

interface Harness {
  lifecycle: PythonServerLifecycle;
  client: HermesRpcClient;
  child: ChildProcess;
  address: string;
}

async function startHarness(onUnexpectedExit?: (message: string) => void): Promise<Harness> {
  let child: ChildProcess | undefined;
  let address = "";
  const lifecycle = new PythonServerLifecycle({
    command: {
      executable: "uv",
      args: ["run", "python", "tests/fixtures/cross_language_server.py"],
    },
    startupTimeoutMs: 5_000,
    shutdownTimeoutMs: 2_000,
    spawn: (command, args, options) => {
      child = nodeSpawn(command, args, options);
      return child;
    },
    createClient: (resolvedAddress) => {
      address = resolvedAddress;
      return new GrpcHermesClient(resolvedAddress);
    },
    onUnexpectedExit,
  });
  const client = await lifecycle.start();
  if (!child) throw new Error("Python 测试进程未启动。");
  return { lifecycle, client, child, address };
}

describe("Node-Python 跨语言进程集成", () => {
  let harness: Harness | undefined;

  afterEach(async () => {
    await harness?.lifecycle.stop();
    harness = undefined;
  });

  it("通过真实 Python 进程完成会话、完整事件流和结构化失败", async () => {
    harness = await startHarness();
    const { client } = harness;
    await client.createSession("session-1");

    const success = await collect(client.runTurn("session-1", "success").events);
    const failure = await collect(client.runTurn("session-1", "failure").events);

    expect(success.map((event) => event.sequence)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(success[6].artifactCreated).toMatchObject({ artifactId: "artifact-1", mediaType: "text/markdown" });
    expect(success[7].turnCompleted?.finalOutput).toBe("answer");
    expect(failure.at(-1)?.turnFailed?.error).toMatchObject({
      code: ErrorCode.ERROR_CODE_PROVIDER_UNAVAILABLE,
      retryable: true,
    });
    expect(failure.at(-1)?.turnFailed?.error?.message).not.toContain("secret");
    await expect(client.getSession("session-1")).resolves.toMatchObject({
      turns: [{ status: 2 }, { status: 3 }],
    });
    await expect(client.listSessions()).resolves.toMatchObject({
      sessions: [{ sessionId: "session-1" }],
    });
  });

  it("将 CancelTurn 传播到 Python runner 并返回唯一取消终态", async () => {
    harness = await startHarness();
    const { client } = harness;
    await client.createSession("session-1");
    const iterator = client.runTurn("session-1", "cancel").events[Symbol.asyncIterator]();
    const started = await iterator.next();
    if (started.done) throw new Error("RunTurn 未返回 turn_started。");

    await expect(client.cancelTurn("session-1", started.value.turnId)).resolves.toMatchObject({ result: 1 });
    const remaining = await collect({ [Symbol.asyncIterator]: () => iterator });

    expect(remaining).toHaveLength(1);
    expect(remaining[0].turnCancelled).toEqual({});
    await expect(client.getSession("session-1")).resolves.toMatchObject({
      turns: [{ status: 4 }],
    });
  });

  it("退出后回收 Python 进程并释放临时端口", async () => {
    harness = await startHarness();
    const { lifecycle, child, address } = harness;
    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));

    await lifecycle.stop();
    await exited;

    expect(child.exitCode).not.toBeNull();
    await expectPortReleased(address);
  });

  it("Python 进程崩溃时结束事件流并报告意外退出", async () => {
    let reportUnexpectedExit: (() => void) | undefined;
    const unexpectedExit = new Promise<void>((resolve) => {
      reportUnexpectedExit = resolve;
    });
    harness = await startHarness(() => reportUnexpectedExit?.());
    const { client } = harness;
    await client.createSession("session-1");

    await expect(collect(client.runTurn("session-1", "crash").events)).rejects.toBeInstanceOf(RpcConnectionError);
    await expect(unexpectedExit).resolves.toBeUndefined();
  });
});
