import { describe, expect, it } from "vitest";

import { MockHermesClient } from "./mock-client.js";

describe("MockHermesClient", () => {
  it("提供无需网络的流式 fixture", async () => {
    const client = new MockHermesClient([
      { sessionId: "mock-session", turnId: "turn-1", sequence: 1, turnStarted: {} },
      { sessionId: "mock-session", turnId: "turn-1", sequence: 2, contentDelta: { text: "增量内容" } },
    ]);
    const received = [];
    for await (const event of client.runTurn("mock-session", "问题").events) received.push(event);

    expect(received).toHaveLength(2);
    expect(received[1].contentDelta?.text).toBe("增量内容");
  });
});
