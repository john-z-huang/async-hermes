import { Server, ServerCredentials, status, type ServiceError } from "@grpc/grpc-js";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  CancelResult,
  HermesAgentService,
  type HermesAgentServer,
  ServingStatus,
  SessionStatus,
  ToolStatus,
  TurnStatus,
} from "../generated/v1/agent.js";
import { RpcProtocolError } from "./health-check.js";
import { GrpcHermesClient, RpcConnectionError, RpcServiceError } from "./hermes-client.js";

function serviceError(code: status, details: string): ServiceError {
  return { code, details, message: details, name: "Error", metadata: undefined as never };
}

async function bind(server: Server): Promise<string> {
  const port = await new Promise<number>((resolve, reject) => {
    server.bindAsync("127.0.0.1:0", ServerCredentials.createInsecure(), (error, boundPort) => {
      if (error) reject(error);
      else resolve(boundPort);
    });
  });
  return `127.0.0.1:${port}`;
}

async function collect<T>(events: AsyncIterable<T>): Promise<T[]> {
  const received: T[] = [];
  for await (const event of events) received.push(event);
  return received;
}

describe("GrpcHermesClient 契约", () => {
  let server: Server;
  let client: GrpcHermesClient | undefined;

  beforeEach(async () => {
    const sessions = new Set<string>();
    const implementation: HermesAgentServer = {
      createSession(call, callback) {
        const sessionId = call.request.sessionId ?? "generated-session";
        sessions.add(sessionId);
        callback(null, { sessionId, status: SessionStatus.SESSION_STATUS_ACTIVE });
      },
      getSession(call, callback) {
        if (!sessions.has(call.request.sessionId)) {
          callback(serviceError(status.NOT_FOUND, "session 不存在。"), null as never);
          return;
        }
        callback(null, {
          sessionId: call.request.sessionId,
          status: SessionStatus.SESSION_STATUS_ACTIVE,
          turns: [{ turnId: "turn-1", status: TurnStatus.TURN_STATUS_COMPLETED }],
        });
      },
      listSessions(_call, callback) {
        callback(null, {
          sessions: [...sessions].map((sessionId) => ({
            sessionId,
            status: SessionStatus.SESSION_STATUS_ACTIVE,
            turns: [],
          })),
        });
      },
      healthCheck(_call, callback) {
        callback(null, {
          status: ServingStatus.SERVING_STATUS_SERVING,
          protocolVersion: "v1",
        });
      },
      runTurn(call) {
        const base = { sessionId: call.request.sessionId, turnId: "turn-1" };
        const events =
          call.request.userInput === "malformed"
            ? [
                { ...base, sequence: 1, turnStarted: {} },
                { ...base, sequence: 3, contentDelta: { text: "gap" } },
              ]
            : [
                { ...base, sequence: 1, turnStarted: {} },
                { ...base, sequence: 2, contentDelta: { text: "an" } },
                { ...base, sequence: 3, contentDelta: { text: "swer" } },
                { ...base, sequence: 4, reasoningDelta: { text: "reason" } },
                {
                  ...base,
                  sequence: 5,
                  toolStarted: { toolName: "inspect_workspace", summary: "读取文件" },
                },
                {
                  ...base,
                  sequence: 6,
                  toolFinished: {
                    toolName: "inspect_workspace",
                    summary: "读取完成",
                    status: ToolStatus.TOOL_STATUS_SUCCEEDED,
                  },
                },
                {
                  ...base,
                  sequence: 7,
                  artifactCreated: {
                    artifactId: "artifact-1",
                    displayName: "报告",
                    mediaType: "text/markdown",
                  },
                },
                { ...base, sequence: 8, turnCompleted: { finalOutput: "answer" } },
              ];
        for (const event of events) call.write(event);
        call.end();
      },
      cancelTurn(_call, callback) {
        callback(null, { result: CancelResult.CANCEL_RESULT_ACCEPTED });
      },
    };
    server = new Server();
    server.addService(HermesAgentService, implementation);
    client = new GrpcHermesClient(await bind(server));
  });

  afterEach(async () => {
    client?.close();
    await new Promise<void>((resolve) => server.tryShutdown(() => resolve()));
  });

  it("通过真实 gRPC 序列化调用全部会话接口并消费完整事件流", async () => {
    if (!client) throw new Error("测试客户端未初始化。");
    await expect(client.healthCheck()).resolves.toMatchObject({ protocolVersion: "v1" });
    await expect(client.createSession("session-1")).resolves.toMatchObject({ sessionId: "session-1" });
    await expect(client.getSession("session-1")).resolves.toMatchObject({
      turns: [{ turnId: "turn-1", status: TurnStatus.TURN_STATUS_COMPLETED }],
    });
    await expect(client.listSessions()).resolves.toMatchObject({
      sessions: [{ sessionId: "session-1" }],
    });

    const events = await collect(client.runTurn("session-1", "question").events);

    expect(events.map((event) => event.sequence)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(events[4].toolStarted?.toolName).toBe("inspect_workspace");
    expect(events[6].artifactCreated?.mediaType).toBe("text/markdown");
    expect(events[7].turnCompleted?.finalOutput).toBe("answer");
  });

  it("区分业务错误并检测 sequence 缺失", async () => {
    if (!client) throw new Error("测试客户端未初始化。");
    await expect(client.getSession("missing")).rejects.toBeInstanceOf(RpcServiceError);
    await client.createSession("session-1");

    await expect(collect(client.runTurn("session-1", "malformed").events)).rejects.toBeInstanceOf(RpcProtocolError);
  });
});

describe("GrpcHermesClient --address 模式 HealthCheck", () => {
  it("指向不可达地址时 HealthCheck 返回连接错误", async () => {
    const client = new GrpcHermesClient("127.0.0.1:59999");
    await expect(client.healthCheck()).rejects.toBeInstanceOf(RpcConnectionError);
    client.close();
  });
});
