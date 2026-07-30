import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { ConfigError, loadConfig } from "./config.js";
import { optionsFromArgs } from "./cli-options.js";

function configFile(body: string): string {
  const path = join(mkdtempSync(join(tmpdir(), "hermes-config-")), "hermes.config.json5");
  writeFileSync(path, body);
  return path;
}

describe("共享 JSON5 配置", () => {
  it("校验与 Python 共用的受版本控制示例", () => {
    expect(loadConfig(resolve("hermes.config.example.json5"))).toMatchObject({
      rpc: { host: "127.0.0.1", port: 50051 },
      agent: { permissions: "read-only" },
      tui: { showReasoning: false },
    });
  });

  it("解析与 Python 相同的共享字段", () => {
    const path = configFile(
      "{ version: 1, rpc: { host: '127.0.0.1', port: 50051, }, agent: { enableReasoning: true, reasonEffect: 'high', }, tui: { showReasoning: true, }, }",
    );
    expect(loadConfig(path)).toMatchObject({
      rpc: { host: "127.0.0.1", port: 50051, startupTimeoutMs: 10000 },
      agent: { enableReasoning: true, reasonEffect: "high" },
      tui: { showReasoning: true },
    });
  });

  it("拒绝不安全或不兼容的字段", () => {
    const path = configFile("{ version: 1, rpc: { host: '0.0.0.0', port: 1 } }");
    expect(() => loadConfig(path)).toThrow(ConfigError);
  });

  it("命令行地址优先于配置", () => {
    const path = configFile("{ version: 1, rpc: { host: '127.0.0.1', port: 50051 }, tui: { showReasoning: true } }");
    expect(optionsFromArgs(["--config", path, "--address", "localhost:60000"])).toEqual({
      address: "localhost:60000",
      showReasoning: true,
    });
  });
});
