import { ensureDefaultConfig, loadConfig } from "./config.js";

export interface CliOptions {
  address: string;
  configPath?: string;
  showReasoning: boolean;
  startPythonServer: boolean;
}

export function optionsFromArgs(
  argv: string[],
  defaultPath: string | null | undefined = ensureDefaultConfig(),
): CliOptions {
  const addressIndex = argv.indexOf("--address");
  const configIndex = argv.indexOf("--config");
  if (argv.includes("--help")) {
    console.log("用法：hermes [--config 路径] [--address host:port]");
    process.exit(0);
  }
  if (configIndex !== -1 && !argv[configIndex + 1]) throw new Error("--config 要求提供配置文件路径。");
  const configPath = configIndex !== -1 && argv[configIndex + 1] ? argv[configIndex + 1] : (defaultPath ?? undefined);
  const config = configPath
    ? loadConfig(configPath, configIndex !== -1 ? (defaultPath ?? undefined) : undefined)
    : undefined;
  const address =
    addressIndex !== -1 && argv[addressIndex + 1]
      ? argv[addressIndex + 1]
      : process.env.HERMES_GRPC_ADDRESS || (config ? `${config.rpc.host}:${config.rpc.port}` : "127.0.0.1:50051");
  return {
    address,
    configPath,
    showReasoning: config?.tui.showReasoning ?? false,
    startPythonServer: addressIndex === -1 && process.env.HERMES_GRPC_ADDRESS === undefined,
  };
}
