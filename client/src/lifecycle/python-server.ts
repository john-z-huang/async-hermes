import { type ChildProcess, spawn as nodeSpawn } from "node:child_process";

import { verifyHealthCheck } from "../rpc/health-check.js";
import { GrpcHermesClient, type HermesRpcClient } from "../rpc/hermes-client.js";

export class PythonServerStartupError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "PythonServerStartupError";
  }
}

export interface PythonServerCommand {
  executable: string;
  args: string[];
}

export function developmentPythonServerCommand(configPath?: string): PythonServerCommand {
  const args = ["run", "hermes-grpc-server", "--host", "127.0.0.1", "--port", "0", "--startup-handshake"];
  if (configPath) args.push("--config", configPath);
  return { executable: process.env.HERMES_PYTHON_EXECUTABLE || "uv", args };
}

type Spawn = (command: string, args: readonly string[], options: { env: NodeJS.ProcessEnv; stdio: "pipe" }) => ChildProcess;

export interface PythonServerLifecycleOptions {
  command: PythonServerCommand;
  startupTimeoutMs?: number;
  shutdownTimeoutMs?: number;
  spawn?: Spawn;
  createClient?: (address: string) => HermesRpcClient;
  onUnexpectedExit?: (message: string) => void;
}

function allowedEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const allowed = ["PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "OPENAI_API_KEY"];
  return Object.fromEntries(allowed.flatMap((key) => (source[key] === undefined ? [] : [[key, source[key]]])));
}

function parseHandshake(line: string): string {
  let data: unknown;
  try {
    data = JSON.parse(line);
  } catch {
    throw new PythonServerStartupError("Python Server 启动握手无效。");
  }
  if (
    !data ||
    typeof data !== "object" ||
    (data as { type?: unknown }).type !== "hermes-started" ||
    typeof (data as { address?: unknown }).address !== "string" ||
    typeof (data as { protocol_version?: unknown }).protocol_version !== "string"
  ) {
    throw new PythonServerStartupError("Python Server 启动握手无效。");
  }
  const address = (data as { address: string }).address;
  if (!/^(127\.0\.0\.1|localhost|\[::1\]):\d+$/.test(address)) {
    throw new PythonServerStartupError("Python Server 启动地址不安全。");
  }
  return address;
}

/** Node 作为父进程时的 Python Server 启动、探测与回收边界。 */
export class PythonServerLifecycle {
  private readonly startupTimeoutMs: number;
  private readonly shutdownTimeoutMs: number;
  private readonly spawn: Spawn;
  private readonly createClient: (address: string) => HermesRpcClient;
  private child: ChildProcess | undefined;
  private client: HermesRpcClient | undefined;
  private stopping = false;

  public constructor(private readonly options: PythonServerLifecycleOptions) {
    this.startupTimeoutMs = options.startupTimeoutMs ?? 10_000;
    this.shutdownTimeoutMs = options.shutdownTimeoutMs ?? 3_000;
    this.spawn = options.spawn ?? nodeSpawn;
    this.createClient = options.createClient ?? ((address) => new GrpcHermesClient(address));
  }

  public async start(): Promise<HermesRpcClient> {
    if (this.client) return this.client;
    const child = this.spawn(this.options.command.executable, this.options.command.args, {
      env: allowedEnvironment(process.env),
      stdio: "pipe",
    });
    this.child = child;
    child.once("exit", () => {
      if (!this.stopping) this.options.onUnexpectedExit?.("Python Server 已意外退出，无法继续接受新 Turn。");
    });
    let address: string;
    try {
      address = await this.waitForHandshake(child);
    } catch (error) {
      await this.stop();
      throw error;
    }
    const client = this.createClient(address);
    try {
      verifyHealthCheck(await client.healthCheck());
    } catch (error) {
      client.close();
      await this.stop();
      throw error;
    }
    this.client = client;
    return client;
  }

  public async stop(): Promise<void> {
    this.stopping = true;
    this.client?.close();
    this.client = undefined;
    const child = this.child;
    this.child = undefined;
    if (!child || child.exitCode !== null) return;
    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    child.kill("SIGTERM");
    const timer = setTimeout(() => child.kill("SIGKILL"), this.shutdownTimeoutMs);
    await exited;
    clearTimeout(timer);
  }

  /** 第二次中断或父进程异常时立即回收，避免遗留孤儿 Python 进程。 */
  public forceStop(): void {
    this.stopping = true;
    this.client?.close();
    this.client = undefined;
    this.child?.kill("SIGKILL");
    this.child = undefined;
  }

  private async waitForHandshake(child: ChildProcess): Promise<string> {
    const stdout = child.stdout;
    if (!stdout) throw new PythonServerStartupError("无法读取 Python Server 启动握手。");
    return new Promise<string>((resolve, reject) => {
      let buffer = "";
      let settled = false;
      const finish = (callback: () => void) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        child.removeListener("error", failed);
        child.removeListener("exit", exited);
        callback();
      };
      const failed = () => finish(() => reject(new PythonServerStartupError("无法启动 Python Server。")));
      const exited = () => finish(() => reject(new PythonServerStartupError("Python Server 在启动完成前退出。")));
      const timeout = setTimeout(
        () => finish(() => reject(new PythonServerStartupError("等待 Python Server 就绪超时。"))),
        this.startupTimeoutMs,
      );
      child.once("error", failed);
      child.once("exit", exited);
      stdout.on("data", (chunk: Buffer | string) => {
        buffer += chunk.toString();
        const newline = buffer.indexOf("\n");
        if (newline === -1) return;
        try {
          const address = parseHandshake(buffer.slice(0, newline));
          finish(() => resolve(address));
        } catch (error) {
          finish(() => reject(error));
        }
      });
    });
  }
}
