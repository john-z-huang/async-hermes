import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { AgentEvent } from "../generated/v1/agent.js";

interface WireFixture {
  name: string;
  wire_hex: string;
  session_id: string;
  turn_id: string;
  sequence: number;
  payload: "content_delta" | null;
  text: string | null;
}

const fixtures = JSON.parse(
  readFileSync(resolve("tests/fixtures/protocol/v1/agent_events.json"), "utf8"),
) as WireFixture[];

describe("共享 v1 wire fixture", () => {
  for (const fixture of fixtures) {
    it(`${fixture.name} 可由 TypeScript 读取`, () => {
      const event = AgentEvent.decode(Buffer.from(fixture.wire_hex, "hex"));

      expect(event).toMatchObject({
        sessionId: fixture.session_id,
        turnId: fixture.turn_id,
        sequence: fixture.sequence,
      });
      expect(event.contentDelta?.text).toBe(fixture.payload === "content_delta" ? fixture.text : undefined);
    });
  }
});
