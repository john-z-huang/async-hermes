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
  let signalCount = 0;
  let app: ReturnType<typeof render> | undefined;
  const stopForSignal = (signal: NodeJS.Signals) => {
    signalCount += 1;
    if (signalCount > 1) {
      lifecycle.forceStop();
      process.exit(128 + (signal === "SIGINT" ? 2 : 15));
    }
    process.exitCode = 128 + (signal === "SIGINT" ? 2 : 15);
    app?.unmount();
  };
  const stopForException = (error: unknown) => {
    console.error(`Hermes 运行时异常：${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
    app?.unmount();
    void lifecycle.stop();
  };
  process.on("SIGINT", stopForSignal);
  process.on("SIGTERM", stopForSignal);
  process.once("uncaughtException", stopForException);
  process.once("unhandledRejection", stopForException);
  try {
    const client = await lifecycle.start();
    app = render(<App client={client} showReasoning={options.showReasoning} />);
    await app.waitUntilExit();
  } catch (error) {
    console.error(`无法启动 Hermes：${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  } finally {
    process.removeListener("SIGINT", stopForSignal);
    process.removeListener("SIGTERM", stopForSignal);
    process.removeListener("uncaughtException", stopForException);
    process.removeListener("unhandledRejection", stopForException);
    await lifecycle.stop();
  }
}

void main();
