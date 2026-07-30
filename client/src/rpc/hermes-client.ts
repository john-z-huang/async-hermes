import { credentials, type ClientReadableStream, type ServiceError } from "@grpc/grpc-js";

import {
  type AgentEvent,
  type CancelTurnResponse,
  type HermesAgentClient as GeneratedHermesAgentClient,
  HermesAgentClient,
  type Session,
} from "../generated/v1/agent.js";

export class RpcConnectionError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "RpcConnectionError";
  }
}

export class RpcServiceError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "RpcServiceError";
  }
}

export interface TurnSubscription {
  events: AsyncIterable<AgentEvent>;
  cancel(): void;
}

/** TUI 依赖的最小 RPC 端口；测试可提供 mock，绝不传递 SDK 历史。 */
export interface HermesRpcClient {
  createSession(sessionId?: string): Promise<Session>;
  runTurn(sessionId: string, userInput: string): TurnSubscription;
  cancelTurn(sessionId: string, turnId: string): Promise<CancelTurnResponse>;
  close(): void;
}

function toRpcError(error: ServiceError): Error {
  const message = error.details || error.message || "RPC 调用失败。";
  return error.code === 14 || error.code === 4 ? new RpcConnectionError(message) : new RpcServiceError(message);
}

async function* streamEvents(stream: ClientReadableStream<AgentEvent>): AsyncIterable<AgentEvent> {
  const buffered: AgentEvent[] = [];
  let completed = false;
  let failure: ServiceError | undefined;
  let wake: (() => void) | undefined;
  const notify = () => {
    wake?.();
    wake = undefined;
  };
  stream.on("data", (event: AgentEvent) => {
    buffered.push(event);
    notify();
  });
  stream.on("error", (error: ServiceError) => {
    failure = error;
    completed = true;
    notify();
  });
  stream.on("end", () => {
    completed = true;
    notify();
  });
  while (!completed || buffered.length > 0) {
    if (buffered.length > 0) {
      yield buffered.shift()!;
      continue;
    }
    await new Promise<void>((resolve) => {
      wake = resolve;
    });
  }
  if (failure) throw toRpcError(failure);
}

/** gRPC 生成客户端的薄包装层，负责 Promise 与流订阅边界。 */
export class GrpcHermesClient implements HermesRpcClient {
  private readonly client: GeneratedHermesAgentClient;

  public constructor(address: string) {
    this.client = new HermesAgentClient(address, credentials.createInsecure());
  }

  public createSession(sessionId?: string): Promise<Session> {
    return new Promise((resolve, reject) => {
      this.client.createSession({ sessionId }, (error, session) => {
        if (error) return reject(toRpcError(error));
        resolve(session);
      });
    });
  }

  public runTurn(sessionId: string, userInput: string): TurnSubscription {
    const stream = this.client.runTurn({ sessionId, userInput });
    return { events: streamEvents(stream), cancel: () => stream.cancel() };
  }

  public cancelTurn(sessionId: string, turnId: string): Promise<CancelTurnResponse> {
    return new Promise((resolve, reject) => {
      this.client.cancelTurn({ sessionId, turnId }, (error, response) => {
        if (error) return reject(toRpcError(error));
        if (response.error) return reject(new RpcServiceError(response.error.message));
        resolve(response);
      });
    });
  }

  public close(): void {
    this.client.close();
  }
}
