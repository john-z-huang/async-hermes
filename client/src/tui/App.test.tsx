import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { CancelResult, type AgentEvent } from "../generated/v1/agent.js";
import { MockHermesClient } from "../rpc/mock-client.js";
import { App } from "./App.js";

const delay = () => new Promise((resolve) => setTimeout(resolve, 20));

class BlockingMockHermesClient extends MockHermesClient {
  public streamCancelled = false;
  private releaseCancellation: (() => void) | undefined;

  public override runTurn(sessionId: string, userInput: string) {
    this.runRequests.push({ sessionId, userInput });
    const cancelled = new Promise<void>((resolve) => {
      this.releaseCancellation = resolve;
    });
    return {
      events: (async function* (): AsyncIterable<AgentEvent> {
        yield { sessionId, turnId: "turn-1", sequence: 1, turnStarted: {} };
        await cancelled;
        yield { sessionId, turnId: "turn-1", sequence: 2, turnCancelled: {} };
      })(),
      cancel: () => {
        this.streamCancelled = true;
        this.releaseCancellation?.();
      },
    };
  }

  public override async cancelTurn(sessionId: string, turnId: string) {
    this.cancelled.push({ sessionId, turnId });
    this.releaseCancellation?.();
    return { result: CancelResult.CANCEL_RESULT_ACCEPTED };
  }
}

describe("Hermes TUI", () => {
  it("使用 mock client 创建会话，不需要网络或 API key", async () => {
    const client = new MockHermesClient();
    const view = render(<App client={client} />);
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(view.lastFrame()).toContain("mock-session");
    expect(view.lastFrame()).toContain("新建会话");
    view.unmount();
  });

  it("将用户输入和完整事件映射到 RPC 与展示状态", async () => {
    const client = new MockHermesClient([
      { sessionId: "mock-session", turnId: "turn-1", sequence: 1, turnStarted: {} },
      { sessionId: "mock-session", turnId: "turn-1", sequence: 2, reasoningDelta: { text: "分析" } },
      { sessionId: "mock-session", turnId: "turn-1", sequence: 3, contentDelta: { text: "答案" } },
      { sessionId: "mock-session", turnId: "turn-1", sequence: 4, turnCompleted: { finalOutput: "完成" } },
    ]);
    const view = render(<App client={client} showReasoning />);
    await delay();

    view.stdin.write("  question  ");
    await delay();
    view.stdin.write("\r");
    await delay();

    expect(client.runRequests).toEqual([{ sessionId: "mock-session", userInput: "question" }]);
    expect(view.lastFrame()).toContain("[reasoning] 分析");
    expect(view.lastFrame()).toContain("[content] 答案");
    expect(view.lastFrame()).toContain("[completed] 完成");
    view.unmount();
  });

  it("优先调用 CancelTurn 并等待服务端取消终态", async () => {
    const client = new BlockingMockHermesClient();
    const view = render(<App client={client} />);
    await delay();
    view.stdin.write("question");
    await delay();
    view.stdin.write("\r");
    await delay();

    view.stdin.write("\x18");
    await delay();

    expect(client.cancelled).toEqual([{ sessionId: "mock-session", turnId: "turn-1" }]);
    expect(client.streamCancelled).toBe(false);
    expect(view.lastFrame()).toContain("[cancelled] 本轮已取消。");
    view.unmount();
  });
});
