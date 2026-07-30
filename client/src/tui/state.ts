import type { AgentEvent, RpcError } from "../generated/v1/agent.js";

export type DisplayEventKind =
  | "content"
  | "reasoning"
  | "tool-started"
  | "tool-finished"
  | "artifact"
  | "completed"
  | "failed"
  | "cancelled";

export interface DisplayEvent {
  kind: DisplayEventKind;
  text: string;
  sequence: number;
}

export interface TurnView {
  id: string;
  lastSequence: number;
  terminal: boolean;
  events: DisplayEvent[];
}

export interface TuiState {
  sessionIds: string[];
  selectedSessionId?: string;
  turns: Record<string, TurnView>;
  connectionError?: string;
  protocolError?: string;
}

export const initialTuiState: TuiState = { sessionIds: [], turns: {} };

export function selectSession(state: TuiState, sessionId: string): TuiState {
  return {
    ...state,
    sessionIds: state.sessionIds.includes(sessionId) ? state.sessionIds : [...state.sessionIds, sessionId],
    selectedSessionId: sessionId,
  };
}

function errorText(error: RpcError | undefined): string {
  return error?.message || "Agent 未返回错误详情。";
}

function displayEvent(event: AgentEvent): DisplayEvent | undefined {
  if (event.contentDelta) return { kind: "content", text: event.contentDelta.text, sequence: event.sequence };
  if (event.reasoningDelta) return { kind: "reasoning", text: event.reasoningDelta.text, sequence: event.sequence };
  if (event.toolStarted) return { kind: "tool-started", text: `${event.toolStarted.toolName}: ${event.toolStarted.summary}`, sequence: event.sequence };
  if (event.toolFinished) return { kind: "tool-finished", text: `${event.toolFinished.toolName}: ${event.toolFinished.summary}`, sequence: event.sequence };
  if (event.artifactCreated) return { kind: "artifact", text: event.artifactCreated.displayName, sequence: event.sequence };
  if (event.turnCompleted) return { kind: "completed", text: event.turnCompleted.finalOutput, sequence: event.sequence };
  if (event.turnFailed) return { kind: "failed", text: errorText(event.turnFailed.error), sequence: event.sequence };
  if (event.turnCancelled) return { kind: "cancelled", text: "本轮已取消。", sequence: event.sequence };
  return undefined;
}

function isTerminal(event: AgentEvent): boolean {
  return Boolean(event.turnCompleted || event.turnFailed || event.turnCancelled);
}

/** 只接受严格连续的服务端事件，拒绝重复、乱序和终态后的数据。 */
export function applyAgentEvent(state: TuiState, event: AgentEvent): TuiState {
  const turn = state.turns[event.turnId] ?? { id: event.turnId, lastSequence: 0, terminal: false, events: [] };
  if (turn.terminal || event.sequence !== turn.lastSequence + 1) {
    return { ...state, protocolError: `Turn ${event.turnId} 收到无效 sequence ${event.sequence}。` };
  }
  const presentation = displayEvent(event);
  const nextTurn: TurnView = {
    ...turn,
    lastSequence: event.sequence,
    terminal: isTerminal(event),
    events: presentation ? [...turn.events, presentation] : turn.events,
  };
  return { ...state, turns: { ...state.turns, [event.turnId]: nextTurn }, protocolError: undefined };
}
