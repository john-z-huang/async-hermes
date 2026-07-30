import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("Node SEA 构建配置", () => {
  it("将 Vite bundle 和 CLI 参数嵌入单一可执行文件", async () => {
    const packageJson = JSON.parse(await readFile(resolve("package.json"), "utf8")) as {
      engines: { node: string };
      scripts: { build: string };
    };
    const seaConfig = JSON.parse(await readFile(resolve("sea-config.json"), "utf8")) as {
      main: string;
      mainFormat: string;
      output: string;
      execArgvExtension: string;
    };

    expect(packageJson.engines.node).toBe(">=25.5.0");
    expect(packageJson.scripts.build).toContain("node --build-sea sea-config.json");
    expect(seaConfig).toMatchObject({
      main: "./dist/cli.js",
      mainFormat: "module",
      output: "./dist/hermes",
      execArgvExtension: "cli",
    });
  });
});
