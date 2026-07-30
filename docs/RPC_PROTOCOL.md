# Hermes v1 RPC 协议

本文档定义 Node 交互层与 Python Agent Service 之间的稳定通信边界。协议源文件为 [`proto/hermes/v1/agent.proto`](../proto/hermes/v1/agent.proto)，使用 `hermes.v1` package。

Python Core 的 `AgentService`、`Session`、`Turn` 和 `AgentEvent` 仍是领域模型；Protobuf 类型仅属于接口层。协议中不得出现 OpenAI Responses API、Agents SDK 原始 item、凭据、环境变量、完整内部异常或 Python 会话历史。

## 服务与会话

`HermesAgent` 提供以下 RPC：

- `CreateSession` 创建进程内会话；可选 `session_id` 未提供时由 Python 生成。
- `RunTurn` 接收 `session_id` 与新增 `user_input`，并以服务端流返回 `AgentEvent`。
- `CancelTurn` 以 `session_id` 与服务端生成的 `turn_id` 请求取消。
- `GetSession` 与 `ListSessions` 只返回会话和 Turn 摘要，不返回 SDK 历史。
- `HealthCheck` 用于本地 Node 父进程确认 Python 服务已就绪；成功时返回 `SERVING_STATUS_SERVING` 与协议版本。

Node 必须只提交新增用户输入，不得缓存、重建或回传 Agent 的结构化历史。同一会话第一版一次只允许一个运行中的 Turn；并发请求应以 `ERROR_CODE_TURN_ALREADY_RUNNING` 被拒绝。

最小请求：

```json
{"sessionId":"session-1","userInput":"总结当前项目"}
```

## 事件流与终态

每个 `AgentEvent` 都有同一 Turn 的 `session_id`、`turn_id` 和从 1 开始严格递增的 `sequence`。`payload` 是可扩展的 `oneof`，当前包含：`turn_started`、`content_delta`、`reasoning_delta`、`tool_started`、`tool_finished`、`artifact_created`、`turn_completed`、`turn_failed`、`turn_cancelled`。

最小事件流中的内容事件：

```json
{"sessionId":"session-1","turnId":"turn-1","sequence":"2","contentDelta":{"text":"这是摘要。"}}
```

客户端必须验证序号连续，且在 `turn_completed`、`turn_failed` 或 `turn_cancelled` 后不再接受该 Turn 的事件。只有 `turn_completed` 会令 Python 提交新的权威历史；失败和取消不得污染此前成功历史。

## 取消、断连与错误

`CancelTurn` 是幂等的：首次接受返回 `CANCEL_RESULT_ACCEPTED`；已结束的 Turn 返回 `CANCEL_RESULT_ALREADY_TERMINAL`。被接受的取消最终必须以 `turn_cancelled` 终止流。

客户端主动取消流或断开连接时，服务端应取消关联执行，且不得继续提交该 Turn 的历史。第一版不支持断线续传或后台继续执行。

流内失败使用 `TurnFailed.error`，其中 `RpcError` 始终包含稳定 `code`、用户可读 `message` 和 `retryable`；可选 `debug_reference` 仅用于安全关联服务端日志。无效请求、未知 session 或传输层失败使用 gRPC 状态，并应携带等价的安全错误信息；不得发送堆栈、密钥或 SDK 原始错误。

## 兼容性与生成

- 已发布字段号、枚举数值和 `oneof` 分支编号永不复用；删除字段必须同时保留名称和编号。
- `v1` 只允许新增可选字段、`oneof` 分支或 RPC 方法。修改字段类型、既有语义或编号属于破坏性变更，必须新建 `hermes.v2`。
- 使用 `./scripts/generate-protocol.sh` 生成 Python 与 TypeScript 类型；生成后的文件必须提交。
- 使用 `./scripts/check-protocol.sh` 运行 Buf lint、与 `main` 的 breaking 检查及生成结果一致性检查。首次引入协议时 `main` 没有 `.proto` 基线，breaking 检查会跳过；基线合并后将自动启用。
- CI 工作流会执行上述检查，以及 `uv run pytest tests/protocol tests/generated`。
