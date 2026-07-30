# Hermes 当前项目架构

本文档描述 Python Core 与 gRPC Server 的项目结构、职责边界和主要运行流程，供后续开发者与 Code Agent 快速了解当前实现。

## 架构目标

Hermes 将 Agent 业务逻辑从具体终端中抽离，使 Python Agent Core/Application Service 成为 CLI 与未来 gRPC Server 的共同入口。

当前依赖方向如下：

```text
main.py
  └─ interfaces/
       ├─ cli.py
       └─ grpc_server.py
       ├─ application/agent_service.py
       │    └─ domain/
       └─ infrastructure/
            ├─ agents_sdk_runner.py
            ├─ workspace_tools.py
            └─ persistence.py
```

依赖必须始终朝向领域和应用层：

- `domain` 不依赖 CLI、gRPC、OpenAI Agents SDK 或文件系统实现；
- `application` 只依赖领域模型，通过 `AgentRunner` 协议调用具体执行器；
- `infrastructure` 实现 Agents SDK、workspace 工具和文件持久化；
- `interfaces` 负责参数、用户输入、终端渲染及进程级装配。

## 代码目录

```text
.
├── main.py
├── hermes/
│   ├── domain/
│   │   ├── events.py
│   │   ├── session.py
│   │   └── turn.py
│   ├── application/
│   │   └── agent_service.py
│   ├── infrastructure/
│   │   ├── agents_sdk_runner.py
│   │   ├── workspace_tools.py
│   │   └── persistence.py
│   └── interfaces/
│       └── cli.py
├── tests/
│   └── test_main.py
├── docs/
│   ├── ARCHITECTURE.md
│   └── GOAL.md
├── pyproject.toml
└── README.md
```

各目录职责如下。

### 命令行入口

`pyproject.toml` 通过 Python console script（控制台脚本）声明：

```toml
[project.scripts]
hermes = "hermes.interfaces.cli:main"
```

执行一次 `uv tool install --editable .` 后，推荐直接使用 `hermes` 命令。可编辑安装会指向当前源码，适合本地开发。

### `main.py`

兼容入口，仅调用 `hermes.interfaces.cli.main()`。原有的 `uv run python main.py` 启动方式保持不变，不应再向此文件加入业务逻辑。

### `hermes/domain/`

保存纯 Python 领域模型：

- `AgentEvent`：应用服务对外产生的稳定、有序事件；
- `Session`：Python 进程内的权威会话及结构化历史；
- `Turn`：单轮输入、状态、最终结果或错误；
- `AgentEventType`、`TurnStatus`：稳定的事件和状态枚举。

领域事件不包含 `[content]` 等终端格式，也不暴露 Agents SDK 原始事件。未来 Protobuf 类型同样不应进入这一层。

### `hermes/application/`

`AgentService` 是当前核心业务入口：

```python
async for event in service.run_turn(session_id, text):
    ...
```

它负责：

- 创建和读取进程内会话；
- 为 Turn 分配标识并产生单调递增的 `sequence`；
- 把 runner 内部事件转换为稳定的 `AgentEvent`；
- 只在收到完整成功事件后提交结构化历史；
- 将异常和取消转换为明确终态；
- 保证失败或不完整的 Turn 不污染此前成功历史。

`AgentRunner` 是应用层端口。测试可注入 fake runner，未来也可替换具体 SDK 实现。

### `hermes/infrastructure/`

`agents_sdk_runner.py` 负责：

- 创建 Agents SDK `Agent`；
- 构造首轮输入，后续轮次追加到结构化历史；
- 将正文、推理和工具调用事件转换为应用层内部事件；
- 在完成事件中交回 `result.to_input_list()` 的完整历史；
- 配置模型推理强度和 workspace function tool。

`workspace_tools.py` 保存原有安全边界：

- 命令和 Git 子命令白名单；
- 禁止修改性参数、shell 管道、重定向和扩展；
- 路径与符号链接必须位于 workspace 内；
- 子进程只继承 `PATH`，不继承凭据环境变量。

`persistence.py` 负责把最终展示结果写入 workspace 内的安全路径。默认文件仍为 `.agents/response-<响应哈希>.txt`。

### `hermes/interfaces/`

`cli.py` 是终端适配器，负责：

- 解析现有命令行参数；
- 读取 loop 模式输入并处理 `/exit`、`/quit` 和空输入；
- 装配 `AgentService` 与 `AgentsSdkRunner`；
- 使用 `EventRenderer` 将领域事件渲染成 `[reasoning]`、`[content]`；
- 控制标准输出、错误提示和响应持久化。

loop 与 one-shot 创建方式不同，但复用同一个 `AgentService.run_turn()`。

`grpc_server.py` 是本地 RPC 适配器，负责：

- 将 Protobuf 请求和领域模型相互转换，不直接读取或写入 Agents SDK 历史；
- 将 `AgentEvent` 转换为单调递增的服务端流，并把内部异常转换为不含敏感信息的 `RpcError`；
- 同一 session 串行执行；不同 session 可并发；
- 将 `CancelTurn`、客户端断开和服务关闭传递为底层 Turn 协程取消；
- 仅绑定 `127.0.0.1`、`::1` 或 `localhost`，默认由系统分配临时端口；
- 在关闭时先停止接收请求、取消活跃 Turn，再停止 gRPC 监听器。

它通过 `HermesGrpcServer` 管理生命周期，并可用 `hermes-grpc-server` 独立启动。`HealthCheck` 在接收新 Turn 时返回 `SERVING`；开始关闭后内部状态转为 `NOT_SERVING`。

## 一轮请求的执行流程

```text
CLI 读取用户输入
  → AgentService.run_turn()
  → 产生 turn_started
  → AgentRunner.run()
  → Agents SDK 流式执行
  → runner 转换 content/reasoning/tool 事件
  → AgentService 补充 session_id、turn_id、sequence
  → CLI EventRenderer 渲染事件
  → turn_completed 时提交结构化历史
  → CLI 持久化展示结果
```

异常时，应用服务产生 `turn_failed`；取消时产生 `turn_cancelled`。这两种情况都不会替换 `Session.history`。runner 未产生完成事件也按失败处理，避免提交不完整历史。

## 会话和状态边界

- Python `Session.history` 是当前进程内的权威结构化历史；
- CLI 不拼接终端输出来重建上下文；
- `--user-prompt` 和 `--content` 仅用于首轮；
- 后续轮次在既有 SDK 历史末尾追加新的 user message；
- 会话暂不跨进程持久化；
- `.agents/` 仅保存便于查看的响应产物，不作为会话恢复来源。

## 后续扩展约束

实现 gRPC Server 时，应在 `hermes/interfaces/` 增加新的 adapter，并继续调用 `AgentService`，不得：

- 在 gRPC handler 中直接操作 Agents SDK 历史；
- 把 Protobuf message 作为核心领域模型；
- 把终端格式加入 `AgentEvent`；
- 将 workspace 权限执行迁移到 Node 或其他客户端；
- 在失败或取消后提交不完整历史。

若新增领域事件，应先更新 `AgentEventType` 和应用层语义，再分别适配 CLI 与 gRPC 表示。

## 验证方式

运行完整测试：

```bash
uv run pytest
```

测试覆盖 workspace 安全边界、SDK 事件转换、事件顺序、成功历史提交、失败保护、CLI 参数与 loop/one-shot 复用。

验证已安装命令：

```bash
hermes --help
```
