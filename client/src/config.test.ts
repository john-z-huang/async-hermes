import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { ConfigError, defaultConfigPath, loadConfig } from "./config.js";
import { optionsFromArgs } from "./cli-options.js";

function configFile(body: string): string {
  const path = join(mkdtempSync(join(tmpdir(), "hermes-config-")), "hermes.config.toml");
  writeFileSync(path, body);
  return path;
}

describe("共享 TOML 配置", () => {
  it("校验与 Python 共用的受版本控制示例", () => {
    expect(loadConfig(resolve("hermes.config.example.toml"))).toMatchObject({
      rpc: { host: "127.0.0.1", port: 50051 },
      agent: { permissions: "read-only" },
      tui: { showReasoning: false },
    });
  });

  it("解析与 Python 相同的共享字段", () => {
    const path = configFile(
      'version = 1\n\n[rpc]\nhost = "127.0.0.1"\nport = 50051\n\n[agent]\nenableReasoning = true\nreasonEffect = "high"\n\n[tui]\nshowReasoning = true',
    );
    expect(loadConfig(path)).toMatchObject({
      rpc: { host: "127.0.0.1", port: 50051, startupTimeoutMs: 10000 },
      agent: { enableReasoning: true, reasonEffect: "high" },
      tui: { showReasoning: true },
    });
  });

  it("拒绝不安全或不兼容的字段", () => {
    const path = configFile('version = 1\n[rpc]\nhost = "0.0.0.0"\nport = 1');
    expect(() => loadConfig(path)).toThrow(ConfigError);
  });

  it("为旧 JSON5 配置提供迁移提示", () => {
    const path = join(mkdtempSync(join(tmpdir(), "hermes-config-")), "hermes.config.json5");
    writeFileSync(path, "{ version: 1 }");
    expect(() => loadConfig(path)).toThrow("已不再支持");
  });

  it("为缺失的 TOML 配置提供诊断", () => {
    const path = join(mkdtempSync(join(tmpdir(), "hermes-config-")), "missing.toml");
    expect(() => loadConfig(path)).toThrow("无法解析 TOML");
  });

  it("命令行地址优先于配置", () => {
    const path = configFile('version = 1\n[rpc]\nhost = "127.0.0.1"\nport = 50051\n[tui]\nshowReasoning = true');
    expect(optionsFromArgs(["--config", path, "--address", "localhost:60000"])).toEqual({
      address: "localhost:60000",
      configPath: path,
      showReasoning: true,
      startPythonServer: false,
    });
  });

  it("环境变量地址优先于配置", () => {
    const path = configFile('version = 1\n[rpc]\nhost = "127.0.0.1"\nport = 50051');
    const original = process.env.HERMES_GRPC_ADDRESS;
    process.env.HERMES_GRPC_ADDRESS = "localhost:60000";
    try {
      expect(optionsFromArgs(["--config", path]).address).toBe("localhost:60000");
    } finally {
      if (original === undefined) delete process.env.HERMES_GRPC_ADDRESS;
      else process.env.HERMES_GRPC_ADDRESS = original;
    }
  });

  it("从用户目录读取默认配置，并允许使用其他 workspace", () => {
    const home = mkdtempSync(join(tmpdir(), "hermes-home-"));
    const directory = join(home, ".async-hermes");
    mkdirSync(directory);
    const path = join(directory, "config.toml");
    writeFileSync(path, 'version = 1\n[rpc]\nhost = "127.0.0.1"\nport = 50051');

    expect(defaultConfigPath(home)).toBe(path);
    expect(optionsFromArgs([], path)).toMatchObject({ address: "127.0.0.1:50051", configPath: path });
  });

  it("调用时的配置字段覆盖用户级默认配置", () => {
    const directory = mkdtempSync(join(tmpdir(), "hermes-config-"));
    const globalPath = join(directory, "global.toml");
    const explicitPath = join(directory, "explicit.toml");
    writeFileSync(globalPath, 'version = 1\n[rpc]\nhost = "127.0.0.1"\nport = 50051\n[agent]\nenableReasoning = false');
    writeFileSync(explicitPath, "[rpc]\nport = 60000\n[agent]\nenableReasoning = true");

    expect(loadConfig(explicitPath, globalPath)).toMatchObject({
      rpc: { host: "127.0.0.1", port: 60000 },
      agent: { enableReasoning: true },
    });
  });

  it("未指定地址时默认由 Node 托管 Python Server", () => {
    expect(optionsFromArgs([], null).startPythonServer).toBe(true);
  });
});
