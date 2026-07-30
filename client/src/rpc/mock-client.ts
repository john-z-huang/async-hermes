import type { AgentEvent, CancelTurnResponse, Session } from "../generated/v1/agent.js";
import type { HermesRpcClient, TurnSubscription } from "./hermes-client.js";

/** 用于 UI 测试的内存 fixture，不依赖网络或真实模型。 */
export class MockHermesClient implements HermesRpcClient {
  public readonly cancelled: Array<{ sessionId: string; turnId: string }> = [];

  public constructor(private readonly scriptedEvents: AgentEvent[] = []) {}

  public async createSession(sessionId = "mock-session"): Promise<Session> {
    return { sessionId, status: 1 };
  }

  public runTurn(_sessionId: string, _userInput: string): TurnSubscription {
    return {
      events: (async function* (events: AgentEvent[]) {
        yield* events;
      })(this.scriptedEvents),
      cancel: () => undefined,
    };
  }

  public async cancelTurn(sessionId: string, turnId: string): Promise<CancelTurnResponse> {
    this.cancelled.push({ sessionId, turnId });
    return { result: 1 };
  }

  public close(): void {}
}
