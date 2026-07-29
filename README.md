# agent-hermes-and-async-batch-tasks
Agent Hermes and Async Batch Tasks

## Workspace inspection tool

The agent exposes workspace access as the strict `inspect_workspace` function
tool. This uses standard structured `function_call` events, so it works with
the OpenAI Responses API and compatible providers that support function tools,
including providers that do not implement the native `shell` tool type.

`inspect_workspace` accepts a required `commands` array. Every command is
validated against the read-only command and Git subcommand allowlists, runs
without a shell, and is confined to the configured `--workspace`. Text such as
`<tool_call>` in a model response is never parsed or executed.

Providers must support structured function calling. Prompt-only or
text-serialized tool calls are not supported.
