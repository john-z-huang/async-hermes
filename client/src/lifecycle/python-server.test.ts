import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";

import { describe, expect, it } from "vitest";

import { ServingStatus, type HealthCheckResponse } from "../generated/v1/agent.js";
import type { HermesRpcClient } from "../rpc/hermes-client.js";
import { developmentPythonServerCommand, PythonServerLifecycle, PythonServerStartupError } from "./python-server.js";

class FakeChild extends EventEmitter {
  public readonly stdout = new PassThrough();
  public exitCode: number | null = null;
  public readonly signals: string[] = [];

  public kill(signal: string): boolean {
    this.signals.push(signal);
    this.exitCode = 0;
    this.emit("exit", 0, signal);
    return true;
  }
}

function client(health: HealthCheckResponse): HermesRpcClient & { closed: boolean } {
  return {
    closed: false,
    healthCheck: async () => health,
    createSession: async () => ({ sessionId: "one", status: 1 }),
    getSession: async (sessionId) => ({ sessionId, status: 1, turns: [] }),
    listSessions: async () => ({ sessions: [] }),
    runTurn: () => ({ events: (async function* () {})(), cancel: () => undefined }),
    cancelTurn: async () => ({ result: 1 }),
    close() {
      this.closed = true;
    },
  };
}

const serving = { status: ServingStatus.SERVING_STATUS_SERVING, protocolVersion: "v1" };

describe("PythonServerLifecycle", () => {
  it("使用参数数组启动、校验握手和健康检查", async () => {
    const child = new FakeChild();
    let invoked: readonly string[] = [];
    const lifecycle = new PythonServerLifecycle({
      command: { executable: "uv", args: ["run", "hermes-grpc-server"] },
      spawn: (_command, args) => {
        invoked = args;
        return child as never;
      },
      createClient: () => client(serving),
    });
    const starting = lifecycle.start();
    child.stdout.write('{"type":"hermes-started","address":"127.0.0.1:54321","protocol_version":"v1"}\n');

    await expect(starting).resolves.toBeDefined();
    expect(invoked).toEqual(["run", "hermes-grpc-server"]);
    await lifecycle.stop();
    expect(child.signals).toEqual(["SIGTERM"]);
  });

  it("拒绝不安全地址和提前退出", async () => {
    const unsafe = new FakeChild();
    const lifecycle = new PythonServerLifecycle({
      command: { executable: "uv", args: [] },
      spawn: () => unsafe as never,
    });
    const starting = lifecycle.start();
    unsafe.stdout.write('{"type":"hermes-started","address":"0.0.0.0:1","protocol_version":"v1"}\n');
    await expect(starting).rejects.toThrow("启动地址不安全");

    const exited = new FakeChild();
    const exiting = new PythonServerLifecycle({
      command: { executable: "uv", args: [] },
      spawn: () => exited as never,
    }).start();
    exited.emit("exit", 1, null);
    await expect(exiting).rejects.toBeInstanceOf(PythonServerStartupError);
  });

  it("启动超时后回收子进程", async () => {
    const child = new FakeChild();
    const lifecycle = new PythonServerLifecycle({
      command: { executable: "uv", args: [] },
      startupTimeoutMs: 5,
      spawn: () => child as never,
    });

    await expect(lifecycle.start()).rejects.toThrow("等待 Python Server 就绪超时");
    expect(child.signals).toEqual(["SIGTERM"]);
  });

  it("启动完成后的异常退出会通知调用方", async () => {
    const child = new FakeChild();
    let message = "";
    const lifecycle = new PythonServerLifecycle({
      command: { executable: "uv", args: [] },
      spawn: () => child as never,
      createClient: () => client(serving),
      onUnexpectedExit: (reported) => {
        message = reported;
      },
    });
    const starting = lifecycle.start();
    child.stdout.write('{"type":"hermes-started","address":"127.0.0.1:54321","protocol_version":"v1"}\n');
    await starting;

    child.emit("exit", 23, null);

    expect(message).toContain("意外退出");
  });

  it("仅向 Python 子进程传递允许的 LLM 运行环境", async () => {
    const child = new FakeChild();
    let childEnvironment: NodeJS.ProcessEnv = {};
    const originalApiKey = process.env.OPENAI_API_KEY;
    const originalBaseUrl = process.env.OPENAI_BASE_URL;
    const originalDefaultModel = process.env.OPENAI_DEFAULT_MODEL;
    const originalUnlisted = process.env.HERMES_UNLISTED_SECRET;
    process.env.OPENAI_API_KEY = "test-api-key";
    process.env.OPENAI_BASE_URL = "https://llm.example.test/v1";
    process.env.OPENAI_DEFAULT_MODEL = "gpt-5";
    process.env.HERMES_UNLISTED_SECRET = "must-not-be-forwarded";
    try {
      const lifecycle = new PythonServerLifecycle({
        command: { executable: "uv", args: [] },
        spawn: (_command, _args, options) => {
          childEnvironment = options.env;
          return child as never;
        },
        createClient: () => client(serving),
      });
      const starting = lifecycle.start();
      child.stdout.write('{"type":"hermes-started","address":"127.0.0.1:54321","protocol_version":"v1"}\n');
      await starting;

      expect(childEnvironment.OPENAI_API_KEY).toBe("test-api-key");
      expect(childEnvironment.OPENAI_BASE_URL).toBe("https://llm.example.test/v1");
      expect(childEnvironment.OPENAI_DEFAULT_MODEL).toBe("gpt-5");
      expect(childEnvironment.HERMES_UNLISTED_SECRET).toBeUndefined();
      await lifecycle.stop();
    } finally {
      if (originalApiKey === undefined) delete process.env.OPENAI_API_KEY;
      else process.env.OPENAI_API_KEY = originalApiKey;
      if (originalBaseUrl === undefined) delete process.env.OPENAI_BASE_URL;
      else process.env.OPENAI_BASE_URL = originalBaseUrl;
      if (originalDefaultModel === undefined) delete process.env.OPENAI_DEFAULT_MODEL;
      else process.env.OPENAI_DEFAULT_MODEL = originalDefaultModel;
      if (originalUnlisted === undefined) delete process.env.HERMES_UNLISTED_SECRET;
      else process.env.HERMES_UNLISTED_SECRET = originalUnlisted;
    }
  });

  it("开发模式使用临时端口和受控握手", () => {
    expect(developmentPythonServerCommand("config.toml", undefined)).toEqual({
      executable: "uv",
      args: [
        "run",
        "hermes-grpc-server",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        "--startup-handshake",
        "--persist-output",
        "--config",
        "config.toml",
      ],
    });
  });

  it("打包运行时仅使用明确指定的 Python 可执行文件", () => {
    expect(developmentPythonServerCommand(undefined, "/app/runtime/python")).toEqual({
      executable: "/app/runtime/python",
      args: [
        "-m",
        "hermes.interfaces.grpc_server",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        "--startup-handshake",
        "--persist-output",
      ],
    });
  });

  it("将 Node Agent 覆盖作为参数数组传给 Python Server", () => {
    expect(
      developmentPythonServerCommand("config.toml", "/app/runtime/python", [
        "--workspace",
        "/workspace",
        "--enable-reasoning",
        "true",
      ]),
    ).toEqual({
      executable: "/app/runtime/python",
      args: [
        "-m",
        "hermes.interfaces.grpc_server",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        "--startup-handshake",
        "--persist-output",
        "--config",
        "config.toml",
        "--workspace",
        "/workspace",
        "--enable-reasoning",
        "true",
      ],
    });
  });

  it("强制停止会立即终止仍在运行的子进程", async () => {
    const child = new FakeChild();
    const lifecycle = new PythonServerLifecycle({
      command: { executable: "uv", args: [] },
      spawn: () => child as never,
      createClient: () => client(serving),
    });
    const starting = lifecycle.start();
    child.stdout.write('{"type":"hermes-started","address":"127.0.0.1:54321","protocol_version":"v1"}\n');
    await starting;

    lifecycle.forceStop();

    expect(child.signals).toEqual(["SIGKILL"]);
  });
});
