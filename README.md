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
py-hermes
py-hermes --running-mode one-shot --question "总结当前项目"
```

`py-hermes` 是原 Python CLI。`--workspace` 省略时仍使用执行命令时的当前目录。源码变更会由可编辑安装立即生效；更新依赖后可执行 `uv tool install --editable . --force` 刷新工具环境。

## 共享 JSON5 配置

`hermes.config.example.json5` 是可提交的非敏感配置示例。复制为本机的
`hermes.config.json5` 后，使用同一个**显式路径**启动各入口：

```bash
py-hermes --config ./hermes.config.json5
uv run hermes-grpc-server --config ./hermes.config.json5
npm run tui -- --config ./hermes.config.json5
```

配置文件必须包含 `version: 1` 与 `rpc.host`、`rpc.port`。支持的共享字段包括
RPC loopback 地址、端口和启动超时，Agent 的 workspace、权限、推理和提示词，以及
TUI 是否显示推理。`agent.workspace` 相对路径以配置文件所在目录为基准；Python 会在
启动前确认它存在，且 workspace/权限安全边界始终由 Python 执行。

优先级为：显式命令行参数 > 已说明的环境变量 > JSON5 文件 > 现有安全内置默认值。
当前仅保留 `HERMES_GRPC_ADDRESS` 作为 TUI 的地址环境变量覆盖；`--address` 优先级更高。
未指定 `--config` 时保留此前 CLI、gRPC Server 和 TUI 的默认行为。JSON5 解析失败、未知
版本、未知字段及非法 host/port/权限会阻止启动并显示诊断信息。

严禁将 `OPENAI_API_KEY`、令牌、密码、完整环境变量或 SDK 历史写入此文件。它已被
`.gitignore` 忽略；只提交示例文件和说明。

原有入口继续兼容：

```bash
uv run python main.py
```

## 本地 gRPC Server

Hermes 也可作为只监听本机回环地址的 gRPC Server 启动。默认使用由系统分配的临时端口；日志会输出实际端口，但不会记录请求正文、凭据或 SDK 原始错误。

```bash
uv run hermes-grpc-server
```

需要指定端口时，仍只能使用 loopback 地址：

```bash
uv run hermes-grpc-server --host 127.0.0.1 --port 50051
```

Node 父进程可调用 `hermes.v1.HermesAgent/HealthCheck`，在服务就绪时会得到 `SERVING_STATUS_SERVING` 与协议版本 `v1`。按 `Ctrl-C` 会停止接收新的 Turn、取消活跃 Turn 并释放端口；同一 session 同时只能运行一个 Turn，不同 session 可以并发执行。

若当前 shell 已安全配置 `OPENAI_API_KEY`，可运行真实 API 冒烟测试。该脚本会启动独立的 `hermes-grpc-server`，执行 `HealthCheck` 和一轮真实模型请求，并自动关闭子进程；不会输出凭据。

```bash
uv run python scripts/smoke_grpc_real_api.py
```

## React TUI

TUI 位于 Node.js/TypeScript workspace，使用 Vite 构建和 React Ink 渲染终端界面。先安装 Node 依赖：

```bash
npm install
```

分别执行质量检查、测试和构建：

```bash
npm run lint
npm run format:check
npm run typecheck
npm test
npm run build
```

`npm run build` 会先将 TUI、生产依赖和项目代码打包为单一脚本，再使用 Node 原生 Single Executable Application（SEA）功能生成本机平台的 `dist/hermes` 可执行文件。构建环境需要 Node.js 25.5.0 或更高版本；不同操作系统与 CPU 架构必须分别构建，产物不应跨平台复用。

默认只需一个 Node 命令。Node 会以子进程启动 Python Server，要求它绑定系统分配的
loopback 临时端口，读取 stdout 中唯一的 JSON 启动握手，再通过 `HealthCheck` 和 `v1`
协议版本校验后才显示可交互 TUI：

```bash
npm run tui -- --config ./hermes.config.json5
```

开发模式使用 `uv run hermes-grpc-server`。打包模式必须由安装器或启动器设置
`HERMES_PYTHON_EXECUTABLE` 为随应用分发的 Python 可执行文件；Node 会以
`<python> -m hermes.interfaces.grpc_server` 调用它，不会搜索用户机器上的任意 Python。
两种模式都通过参数数组启动，不拼接 shell 命令；子进程只继承明确白名单中的环境变量。

如需调试已单独启动的本地 Server，可显式使用 `--address`（或
`HERMES_GRPC_ADDRESS`），此时 TUI 不管理该进程：

```bash
uv run hermes-grpc-server --config ./hermes.config.json5
npm run tui -- --address 127.0.0.1:50051
```

也可在构建后直接运行二进制文件：

```bash
./dist/hermes --config ./hermes.config.json5
```

TUI 只保存会话选择和展示事件，不保存或重建 Agents SDK 历史。快捷键：`n` 新建会话、`Tab` 切换会话、`Ctrl+X` 取消、方向键滚动、`Ctrl+C` 退出。
正常退出、`SIGINT`、`SIGTERM` 或未捕获异常都会关闭 gRPC client，并向 Python 发送
`SIGTERM`；超时后升级为 `SIGKILL`。连续第二次中断会立即强制回收子进程。

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

`py-hermes` defaults to an interactive `loop` session:

```bash
py-hermes
```

Enter one question per prompt. Empty input is ignored; enter `/exit` or `/quit`
to end the session. An optional `--question` is executed as the first turn
before the program starts reading from standard input:

```bash
py-hermes \
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
py-hermes \
  --running-mode one-shot \
  --question "Summarize this project"
```

`--question` must be non-empty in `one-shot` mode. Streaming, reasoning, and
response persistence options work in both modes. Without `--output-file`, each
response is saved under `.agents/response-<response-hash>.txt`. A fixed
`--output-file` is overwritten by each successful loop turn, so it always
contains the latest response.
