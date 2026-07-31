import { ensureDefaultConfig, loadConfig } from "./config.js";

export type RunningMode = "loop" | "one-shot";

export interface CliOptions {
  address: string;
  configPath?: string;
  enableStreamOutput: boolean;
  externalServerNotice?: string;
  initialQuestion?: string;
  pythonServerArgs: string[];
  runningMode: RunningMode;
  showReasoning: boolean;
  startPythonServer: boolean;
}

interface ParsedArguments {
  address?: string;
  configPath?: string;
  enableReasoning?: boolean;
  enableStreamOutput: boolean;
  hasServerOverrides: boolean;
  initialQuestion?: string;
  pythonServerArgs: string[];
  runningMode: RunningMode;
}

const REASON_EFFECTS = new Set(["minimal", "low", "medium", "high", "xhigh", "max"]);
const SERVER_VALUE_OPTIONS = new Map<string, Set<string> | undefined>([
  ["--system-prompt", undefined],
  ["--user-prompt", undefined],
  ["--content", undefined],
  ["--default-model", undefined],
  ["--reason-effect", REASON_EFFECTS],
  ["--workspace", undefined],
  ["--output-file", undefined],
  ["--permissions", new Set(["read-only"])],
]);

const HELP = `用法：hermes [选项]

连接选项：
  --config 路径
  --address host:port

交互选项：
  --question 内容
  --running-mode loop|one-shot
  --enable-stream-output [true|false]

Agent 选项（仅适用于 Node 管理的 Python Server）：
  --system-prompt 内容
  --user-prompt 内容
  --content 内容
  --default-model 模型名称
  --enable-reasoning [true|false]
  --reason-effect minimal|low|medium|high|xhigh|max
  --workspace 路径
  --output-file workspace内相对路径
  --permissions read-only`;

function valueAfter(argv: string[], index: number, option: string, allowEmpty = false): string {
  const value = argv[index + 1];
  if (value === undefined || value.startsWith("--") || (!allowEmpty && !value.trim()))
    throw new Error(`${option} 要求提供${allowEmpty ? "字符串" : "非空值"}。`);
  return value;
}

function booleanAfter(argv: string[], index: number, option: string): { consumed: number; value: boolean } {
  const candidate = argv[index + 1];
  if (candidate === undefined || candidate.startsWith("--")) return { consumed: 0, value: true };
  const normalized = candidate.toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return { consumed: 1, value: true };
  if (["0", "false", "no", "off"].includes(normalized)) return { consumed: 1, value: false };
  throw new Error(`${option} 必须使用 true 或 false。`);
}

function parseArguments(argv: string[]): ParsedArguments {
  const parsed: ParsedArguments = {
    enableStreamOutput: true,
    hasServerOverrides: false,
    pythonServerArgs: [],
    runningMode: "loop",
  };
  for (let index = 0; index < argv.length; index += 1) {
    const option = argv[index];
    if (option === "--help") {
      console.log(HELP);
      process.exit(0);
    }
    if (option === "--config") {
      parsed.configPath = valueAfter(argv, index, option);
      index += 1;
      continue;
    }
    if (option === "--address") {
      parsed.address = valueAfter(argv, index, option);
      index += 1;
      continue;
    }
    if (option === "--question") {
      parsed.initialQuestion = valueAfter(argv, index, option);
      index += 1;
      continue;
    }
    if (option === "--running-mode") {
      const value = valueAfter(argv, index, option);
      if (value !== "loop" && value !== "one-shot") throw new Error("--running-mode 必须是 loop 或 one-shot。");
      parsed.runningMode = value;
      index += 1;
      continue;
    }
    if (option === "--enable-stream-output") {
      const result = booleanAfter(argv, index, option);
      parsed.enableStreamOutput = result.value;
      index += result.consumed;
      continue;
    }
    if (option === "--enable-reasoning") {
      const result = booleanAfter(argv, index, option);
      parsed.enableReasoning = result.value;
      parsed.pythonServerArgs.push(option, String(result.value));
      parsed.hasServerOverrides = true;
      index += result.consumed;
      continue;
    }
    if (SERVER_VALUE_OPTIONS.has(option)) {
      const allowEmpty = option === "--content";
      const value = valueAfter(argv, index, option, allowEmpty);
      const choices = SERVER_VALUE_OPTIONS.get(option);
      if (choices && !choices.has(value)) throw new Error(`${option} 必须是以下值之一：${[...choices].join(", ")}。`);
      parsed.pythonServerArgs.push(option, value);
      parsed.hasServerOverrides = true;
      index += 1;
      continue;
    }
    throw new Error(`未知选项：${option}。使用 --help 查看可用选项。`);
  }
  if (parsed.runningMode === "one-shot" && !parsed.initialQuestion?.trim())
    throw new Error("--running-mode one-shot 要求提供非空的 --question。");
  return parsed;
}

export function optionsFromArgs(
  argv: string[],
  defaultPath: string | null | undefined = ensureDefaultConfig(),
): CliOptions {
  const parsed = parseArguments(argv);
  const configPath = parsed.configPath ?? defaultPath ?? undefined;
  const config = configPath
    ? loadConfig(configPath, parsed.configPath !== undefined ? (defaultPath ?? undefined) : undefined)
    : undefined;
  const environmentAddress = process.env.HERMES_GRPC_ADDRESS;
  const startPythonServer = parsed.address === undefined && environmentAddress === undefined;
  if (!startPythonServer && parsed.hasServerOverrides)
    throw new Error("连接外部 Python Server 时不能使用 Agent 覆盖参数；请在外部 Server 启动命令或其 TOML 配置中设置。");
  const address =
    parsed.address ?? environmentAddress ?? (config ? `${config.rpc.host}:${config.rpc.port}` : "127.0.0.1:50051");
  const hasConfiguredAgent = Boolean(config && Object.values(config.agent).some((value) => value !== undefined));
  return {
    address,
    configPath,
    enableStreamOutput: parsed.enableStreamOutput,
    externalServerNotice:
      !startPythonServer && hasConfiguredAgent
        ? "提示：当前连接外部 Python Server，本地 TOML 的 [agent] 配置不会应用；Agent 配置由外部服务决定。"
        : undefined,
    initialQuestion: parsed.initialQuestion,
    pythonServerArgs: parsed.pythonServerArgs,
    runningMode: parsed.runningMode,
    showReasoning: parsed.enableReasoning ?? config?.tui.showReasoning ?? false,
    startPythonServer,
  };
}
