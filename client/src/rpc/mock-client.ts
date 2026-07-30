import {
  ServingStatus,
  type AgentEvent,
  type CancelTurnResponse,
  type HealthCheckResponse,
  type ListSessionsResponse,
  type Session,
  type SessionSnapshot,
} from "../generated/v1/agent.js";
import type { HermesRpcClient, TurnSubscription } from "./hermes-client.js";

/** 用于 UI 测试的内存 fixture，不依赖网络或真实模型。 */
export class MockHermesClient implements HermesRpcClient {
  public readonly cancelled: Array<{ sessionId: string; turnId: string }> = [];
  public readonly runRequests: Array<{ sessionId: string; userInput: string }> = [];
  private readonly sessions = new Map<string, Session>();

  public constructor(private readonly scriptedEvents: AgentEvent[] = []) {}

  public async healthCheck(): Promise<HealthCheckResponse> {
    return { status: ServingStatus.SERVING_STATUS_SERVING, protocolVersion: "v1" };
  }

  public async createSession(sessionId = "mock-session"): Promise<Session> {
    const session = { sessionId, status: 1 };
    this.sessions.set(sessionId, session);
    return session;
  }

  public async getSession(sessionId: string): Promise<SessionSnapshot> {
    if (!this.sessions.has(sessionId)) throw new Error("session 不存在。");
    return { sessionId, status: 1, turns: [] };
  }

  public async listSessions(): Promise<ListSessionsResponse> {
    return {
      sessions: [...this.sessions.values()].map((session) => ({
        sessionId: session.sessionId,
        status: session.status,
        turns: [],
      })),
    };
  }

  public runTurn(sessionId: string, userInput: string): TurnSubscription {
    this.runRequests.push({ sessionId, userInput });
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
