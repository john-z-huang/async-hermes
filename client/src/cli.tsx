#!/usr/bin/env node
import { render } from "ink";

import { GrpcHermesClient } from "./rpc/hermes-client.js";
import { App } from "./tui/App.js";

function addressFromArgs(argv: string[]): string {
  const addressIndex = argv.indexOf("--address");
  if (argv.includes("--help")) {
    console.log("用法：hermes [--address host:port]");
    process.exit(0);
  }
  if (addressIndex !== -1 && argv[addressIndex + 1]) return argv[addressIndex + 1];
  return process.env.HERMES_GRPC_ADDRESS || "127.0.0.1:50051";
}

render(<App client={new GrpcHermesClient(addressFromArgs(process.argv.slice(2)))} />);
