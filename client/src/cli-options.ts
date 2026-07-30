import { loadConfig } from "./config.js";

export function optionsFromArgs(argv: string[]): { address: string; showReasoning: boolean } {
  const addressIndex = argv.indexOf("--address");
  const configIndex = argv.indexOf("--config");
  if (argv.includes("--help")) {
    console.log("用法：hermes [--config 路径] [--address host:port]");
    process.exit(0);
  }
  if (configIndex !== -1 && !argv[configIndex + 1]) throw new Error("--config 要求提供配置文件路径。");
  const config = configIndex !== -1 && argv[configIndex + 1] ? loadConfig(argv[configIndex + 1]) : undefined;
  const address =
    addressIndex !== -1 && argv[addressIndex + 1]
      ? argv[addressIndex + 1]
      : process.env.HERMES_GRPC_ADDRESS || (config ? `${config.rpc.host}:${config.rpc.port}` : "127.0.0.1:50051");
  return { address, showReasoning: config?.tui.showReasoning ?? false };
}
