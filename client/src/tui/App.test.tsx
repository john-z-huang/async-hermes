import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";

import { MockHermesClient } from "../rpc/mock-client.js";
import { App } from "./App.js";

describe("Hermes TUI", () => {
  it("使用 mock client 创建会话，不需要网络或 API key", async () => {
    const client = new MockHermesClient();
    const view = render(<App client={client} />);
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(view.lastFrame()).toContain("mock-session");
    expect(view.lastFrame()).toContain("新建会话");
    view.unmount();
  });
});
