import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { extname, join, resolve } from "node:path";

import { parse } from "smol-toml";

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "localhost"]);
const REASON_EFFECTS = new Set(["minimal", "low", "medium", "high", "xhigh", "max"]);

export class ConfigError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

export interface HermesConfig {
  path: string;
  rpc: { host: string; port: number; startupTimeoutMs: number };
  agent: {
    workspace?: string;
    permissions?: string;
    enableReasoning?: boolean;
    reasonEffect?: string;
    systemPrompt?: string;
    userPrompt?: string;
    content?: string;
  };
  tui: { showReasoning: boolean };
}

export function defaultConfigPath(homeDirectory = homedir()): string | undefined {
  const path = join(homeDirectory, ".async-hermes", "config.toml");
  return existsSync(path) ? path : undefined;
}

function object(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ConfigError(`${field} 必须是对象。`);
  return value as Record<string, unknown>;
}

function unknown(values: Record<string, unknown>, allowed: string[], field: string) {
  const unexpected = Object.keys(values).filter((key) => !allowed.includes(key));
  if (unexpected.length) throw new ConfigError(`${field} 包含未知字段：${unexpected.sort().join(", ")}。`);
}

function text(values: Record<string, unknown>, field: string, required = false): string | undefined {
  const value = values[field];
  if (value === undefined && !required) return undefined;
  if (typeof value !== "string" || !value.trim()) throw new ConfigError(`${field} 必须是非空字符串。`);
  return value;
}

function boolean(values: Record<string, unknown>, field: string): boolean | undefined {
  const value = values[field];
  if (value === undefined) return undefined;
  if (typeof value !== "boolean") throw new ConfigError(`${field} 必须是布尔值。`);
  return value;
}

export function loadConfig(configPath: string): HermesConfig {
  const path = resolve(configPath);
  if (extname(path).toLowerCase() === ".json5")
    throw new ConfigError(
      `JSON5 配置文件已不再支持：${path}。请将其迁移为 TOML，并使用 hermes.config.example.toml 作为示例。`,
    );
  let parsed: unknown;
  try {
    parsed = parse(readFileSync(path, "utf8"));
  } catch (error) {
    throw new ConfigError(`无法解析 TOML 配置文件 ${path}：${String(error)}`);
  }
  const root = object(parsed, "配置根");
  unknown(root, ["version", "rpc", "agent", "tui"], "配置根");
  if (root.version !== 1) throw new ConfigError("仅支持配置版本 1。");
  const rpc = object(root.rpc, "rpc");
  unknown(rpc, ["host", "port", "startupTimeoutMs"], "rpc");
  const host = text(rpc, "host", true)!;
  if (!LOOPBACK_HOSTS.has(host)) throw new ConfigError("rpc.host 只能是 loopback 地址。");
  if (!Number.isInteger(rpc.port) || (rpc.port as number) < 0 || (rpc.port as number) > 65535)
    throw new ConfigError("rpc.port 必须是 0 到 65535 的整数。");
  const startupTimeoutMs = rpc.startupTimeoutMs === undefined ? 10000 : rpc.startupTimeoutMs;
  if (!Number.isInteger(startupTimeoutMs) || (startupTimeoutMs as number) <= 0)
    throw new ConfigError("rpc.startupTimeoutMs 必须是正整数。");
  const agent = object(root.agent ?? {}, "agent");
  unknown(
    agent,
    ["workspace", "permissions", "enableReasoning", "reasonEffect", "systemPrompt", "userPrompt", "content"],
    "agent",
  );
  const permissions = text(agent, "permissions");
  if (permissions !== undefined && permissions !== "read-only")
    throw new ConfigError(`agent.permissions 不受支持：${permissions}。`);
  const reasonEffect = text(agent, "reasonEffect");
  if (reasonEffect !== undefined && !REASON_EFFECTS.has(reasonEffect))
    throw new ConfigError(`agent.reasonEffect 不受支持：${reasonEffect}。`);
  const tui = object(root.tui ?? {}, "tui");
  unknown(tui, ["showReasoning"], "tui");
  return {
    path,
    rpc: { host, port: rpc.port as number, startupTimeoutMs: startupTimeoutMs as number },
    agent: {
      workspace: text(agent, "workspace"),
      permissions,
      enableReasoning: boolean(agent, "enableReasoning"),
      reasonEffect,
      systemPrompt: text(agent, "systemPrompt"),
      userPrompt: text(agent, "userPrompt"),
      content: text(agent, "content"),
    },
    tui: { showReasoning: boolean(tui, "showReasoning") ?? false },
  };
}
