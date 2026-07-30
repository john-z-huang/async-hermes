#!/usr/bin/env node
import { render } from "ink";

import { optionsFromArgs } from "./cli-options.js";
import { developmentPythonServerCommand, PythonServerLifecycle } from "./lifecycle/python-server.js";
import { GrpcHermesClient } from "./rpc/hermes-client.js";
import { App } from "./tui/App.js";

const options = optionsFromArgs(process.argv.slice(2));

async function main(): Promise<void> {
  if (!options.startPythonServer) {
    render(<App client={new GrpcHermesClient(options.address)} showReasoning={options.showReasoning} />);
    return;
  }
  const lifecycle = new PythonServerLifecycle({
    command: developmentPythonServerCommand(options.configPath),
    onUnexpectedExit: (message) => console.error(message),
  });
  try {
    const client = await lifecycle.start();
    const app = render(<App client={client} showReasoning={options.showReasoning} />);
    await app.waitUntilExit();
  } catch (error) {
    console.error(`无法启动 Hermes：${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  } finally {
    await lifecycle.stop();
  }
}

void main();
