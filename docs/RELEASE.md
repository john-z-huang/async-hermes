# Hermes 发布、安装与故障排查

本文描述第一版本地双运行时发布包。交互模式使用同一目录中的 Node TUI 与 Python Server；
不需要系统 Python，也不连接远程 RPC 服务。

## 支持矩阵

每次 Pull Request 与 `main` 构建均在下列原生 runner 上运行 clean build、Python/Node 质量门禁、
跨语言契约测试和冻结 Server 启动握手测试。

| 平台 | 架构 | 发布目录标识 | 状态 |
| --- | --- | --- | --- |
| macOS | arm64 | `macos-arm64` | 支持 |
| Linux | x64 | `linux-x64` | 支持 |
| Windows | x64 | `win32-x64` | 支持 |

其他系统或架构在获得对应 CI 验证前均不支持。每个产物只能在其匹配的平台上使用。

## 安装

1. 下载与本机平台匹配的发布归档，并完整解压到新目录；不得只复制 `hermes` 或
   `hermes-server` 其中之一。
2. 校验归档内的 `checksums.sha256`。校验失败时删除该新目录，不要启动其中任何文件。
3. macOS/Linux 首次使用前确保两个二进制有执行权限；Windows 使用 `.exe` 文件。
4. 运行发布目录中的 `hermes`。该命令是唯一的交互入口，自动启动同目录的
   `hermes-server`。

发布目录应至少包含：`hermes`、`hermes-server`、`release-manifest.json`、
`dependency-inventory.json` 和 `checksums.sha256`。不应包含 `.env`、API key、`.agents/`、
测试响应或用户 workspace 数据。

## 升级与回滚

为避免失败安装破坏可用版本，使用版本化目录和原子切换：

1. 将新版本解压到新的目录，例如 `hermes-0.1.0/`，保留旧目录不动。
2. 校验 checksum，并运行新目录的 `hermes` 完成一次启动检查。
3. 仅在检查成功后，将启动脚本、快捷方式或 shell 别名切换到新目录。
4. 若启动提示版本不兼容、校验失败或初始化失败，将入口切回旧目录；不要混用两个目录中的
   二进制。

版本握手要求发布版本、Python 包版本与 Protocol 版本匹配。不兼容错误会提示重新安装同一发布
版本或回滚；它不会静默连接到不匹配的 Server。

## 卸载

退出 Hermes 后删除不再使用的版本化发布目录及其快捷方式/别名即可。默认配置位于
`~/.async-hermes/config.toml`，仅当确定不再需要个人运行设置时才单独删除；卸载发布包不会自动
删除该文件或用户 workspace 中的内容。

## 故障排查

- **checksum 不匹配**：删除新下载目录，重新获取完整归档；不要从旧版本复制单个文件补齐。
- **Python Server 版本不兼容**：确认启动目录中两个二进制来自同一归档；重新安装或回滚。
- **Server 未启动/握手超时**：检查 `hermes-server` 是否存在且可执行，并查看终端错误输出；
  不要通过设置任意 Python 路径绕过内置 Server。
- **需要独立自动化或 one-shot**：安装 Python wheel 后使用 `py-hermes --running-mode one-shot`；
  该路径不替代交互发布包。
- **需要连接调试 Server**：开发模式可使用 `--address` 或 `HERMES_GRPC_ADDRESS` 连接显式的
  loopback 地址；此时 TUI 不管理该 Server 生命周期。

## 发布者检查清单

发布前必须完成 Protocol 生成/兼容性验证、Python/Node 测试、#13 跨语言测试和每个支持平台的
`npm run build:release -- <target>`。候选目录的 `checksums.sha256` 与
`dependency-inventory.json` 必须随归档一同发布；代码签名与正式发布上传在后续发布自动化中执行。
