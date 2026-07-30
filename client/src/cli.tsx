#!/usr/bin/env node
import { render } from "ink";

import { optionsFromArgs } from "./cli-options.js";
import { GrpcHermesClient } from "./rpc/hermes-client.js";
import { App } from "./tui/App.js";

const options = optionsFromArgs(process.argv.slice(2));
render(<App client={new GrpcHermesClient(options.address)} showReasoning={options.showReasoning} />);
