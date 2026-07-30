# agent-hermes-and-async-batch-tasks
Agent Hermes and Async Batch Tasks

## 项目架构

Python 代码已拆分为领域层、应用层、基础设施层和接口层。`main.py` 仅作为兼容 CLI 入口；`AgentService` 是 loop、one-shot 和未来 gRPC Server 共用的业务入口。

详细的目录职责、依赖方向、事件流和后续扩展约束见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 安装与启动

首次在仓库目录中安装可编辑的命令行工具：

```bash
uv tool install --editable .
```

安装后可以在任意目录直接运行：

```bash
hermes
hermes --running-mode one-shot --question "总结当前项目"
```

`--workspace` 省略时仍使用执行命令时的当前目录。源码变更会由可编辑安装立即生效；更新依赖后可执行 `uv tool install --editable . --force` 刷新工具环境。

原有入口继续兼容：

```bash
uv run python main.py
```

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

## Running modes

`hermes` defaults to an interactive `loop` session:

```bash
hermes
```

Enter one question per prompt. Empty input is ignored; enter `/exit` or `/quit`
to end the session. An optional `--question` is executed as the first turn
before the program starts reading from standard input:

```bash
hermes \
  --running-mode loop \
  --question "Summarize this project"
```

Successful turns share one in-memory conversation. The Agents SDK's structured
history is retained, including assistant messages, reasoning items, function
calls, and function outputs. `--user-prompt` and `--content` are added only to
the first turn. The system prompt, model settings, workspace, and permissions
continue to apply to every turn. Failed turns do not change the accumulated
history, and history is discarded when the process exits.

Use `one-shot` when exactly one request should run:

```bash
hermes \
  --running-mode one-shot \
  --question "Summarize this project"
```

`--question` must be non-empty in `one-shot` mode. Streaming, reasoning, and
response persistence options work in both modes. Without `--output-file`, each
response is saved under `.agents/response-<response-hash>.txt`. A fixed
`--output-file` is overwritten by each successful loop turn, so it always
contains the latest response.
