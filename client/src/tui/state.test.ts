import { describe, expect, it } from "vitest";

import type { AgentEvent } from "../generated/v1/agent.js";
import { applyAgentEvent, initialTuiState, selectSession } from "./state.js";

const started: AgentEvent = { sessionId: "session-1", turnId: "turn-1", sequence: 1, turnStarted: {} };
const content: AgentEvent = { sessionId: "session-1", turnId: "turn-1", sequence: 2, contentDelta: { text: "你好" } };

describe("TUI state", () => {
  it("保存展示事件而不保存 Agent SDK 历史", () => {
    const afterStart = applyAgentEvent(selectSession(initialTuiState, "session-1"), started);
    const state = applyAgentEvent(afterStart, content);
    expect(state.selectedSessionId).toBe("session-1");
    expect(state.turns["turn-1"].events).toEqual([{ kind: "content", text: "你好", sequence: 2 }]);
    expect(state).not.toHaveProperty("history");
  });

  it("拒绝重复或乱序事件", () => {
    const afterStart = applyAgentEvent(initialTuiState, started);
    const duplicate = applyAgentEvent(afterStart, started);
    const outOfOrder = applyAgentEvent(afterStart, { ...content, sequence: 3 });
    expect(duplicate.protocolError).toContain("无效 sequence 1");
    expect(outOfOrder.protocolError).toContain("无效 sequence 3");
    expect(duplicate.turns["turn-1"].lastSequence).toBe(1);
  });

  it("以结构化错误作为终态", () => {
    const afterStart = applyAgentEvent(initialTuiState, started);
    const failed = applyAgentEvent(afterStart, {
      sessionId: "session-1",
      turnId: "turn-1",
      sequence: 2,
      turnFailed: { error: { code: 7, message: "服务内部错误。", retryable: true } },
    });
    expect(failed.turns["turn-1"].terminal).toBe(true);
    expect(failed.turns["turn-1"].events[0]).toMatchObject({ kind: "failed", text: "服务内部错误。" });
  });

  it("拒绝终态之后的增量事件", () => {
    const afterStart = applyAgentEvent(initialTuiState, started);
    const completed = applyAgentEvent(afterStart, {
      sessionId: "session-1",
      turnId: "turn-1",
      sequence: 2,
      turnCompleted: { finalOutput: "完成" },
    });
    const late = applyAgentEvent(completed, { ...content, sequence: 3 });

    expect(late.protocolError).toContain("无效 sequence 3");
    expect(late.turns["turn-1"].events).toHaveLength(1);
  });
});
