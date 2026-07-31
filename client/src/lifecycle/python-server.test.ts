import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";

import { describe, expect, it } from "vitest";

import { ServingStatus, type HealthCheckResponse } from "../generated/v1/agent.js";
import type { HermesRpcClient } from "../rpc/hermes-client.js";
import {
  developmentPythonServerCommand,
  isPackagedSeaRuntime,
  packagedPythonServerCommand,
  PythonServerLifecycle,
  PythonServerStartupError,
} from "./python-server.js";

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
    child.stdout.write('{"type":"hermes-started","address":"127.0.0.1:54321","protocol_version":"v1","release_version":"0.1.0","python_package_version":"0.1.0"}\n');

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
    unsafe.stdout.write('{"type":"hermes-started","address":"0.0.0.0:1","protocol_version":"v1","release_version":"0.1.0","python_package_version":"0.1.0"}\n');
    await expect(starting).rejects.toThrow("启动地址不安全");

    const exited = new FakeChild();
    const exiting = new PythonServerLifecycle({
      command: { executable: "uv", args: [] },
      spawn: () => exited as never,
    }).start();
    exited.emit("exit", 1, null);
    await expect(exiting).rejects.toBeInstanceOf(PythonServerStartupError);
  });

  it("拒绝版本不匹配的 Python Server", async () => {
    const child = new FakeChild();
    const lifecycle = new PythonServerLifecycle({
      command: { executable: "hermes-server", args: [] },
      spawn: () => child as never,
    });
    const starting = lifecycle.start();
    child.stdout.write('{"type":"hermes-started","address":"127.0.0.1:54321","protocol_version":"v1","release_version":"0.2.0","python_package_version":"0.2.0"}\n');

    await expect(starting).rejects.toThrow("Python Server 版本不兼容");
    expect(child.signals).toEqual(["SIGTERM"]);
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
    child.stdout.write('{"type":"hermes-started","address":"127.0.0.1:54321","protocol_version":"v1","release_version":"0.1.0","python_package_version":"0.1.0"}\n');
    await starting;

    child.emit("exit", 23, null);

    expect(message).toContain("意外退出");
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
        "--config",
        "config.toml",
      ],
    });
  });

  it("打包运行时仅使用明确指定的 Python 可执行文件", () => {
    expect(developmentPythonServerCommand(undefined, "/app/runtime/python")).toEqual({
      executable: "/app/runtime/python",
      args: ["-m", "hermes.interfaces.grpc_server", "--host", "127.0.0.1", "--port", "0", "--startup-handshake"],
    });
  });

  it("发布运行时从同目录的受控 Server 二进制启动", () => {
    expect(packagedPythonServerCommand("/app/hermes-server")).toEqual({
      executable: "/app/hermes-server",
      args: ["--host", "127.0.0.1", "--port", "0", "--startup-handshake"],
    });
    expect(isPackagedSeaRuntime({ sea: "1" } as unknown as NodeJS.ProcessVersions)).toBe(true);
    expect(isPackagedSeaRuntime({ node: "26" } as unknown as NodeJS.ProcessVersions)).toBe(false);
  });

  it("强制停止会立即终止仍在运行的子进程", async () => {
    const child = new FakeChild();
    const lifecycle = new PythonServerLifecycle({
      command: { executable: "uv", args: [] },
      spawn: () => child as never,
      createClient: () => client(serving),
    });
    const starting = lifecycle.start();
    child.stdout.write('{"type":"hermes-started","address":"127.0.0.1:54321","protocol_version":"v1","release_version":"0.1.0","python_package_version":"0.1.0"}\n');
    await starting;

    lifecycle.forceStop();

    expect(child.signals).toEqual(["SIGKILL"]);
  });
});
