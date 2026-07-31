# ADR-0001：第一版采用平台化 Node 与 Python Server 产物

## 状态

已采纳（2026-07-31）。

## 背景

Hermes 的交互模式需要由 Node TUI 启动本地 Python gRPC Server。发布版本必须让用户通过
一个入口使用该组合，同时保留可独立安装的 Python headless/one-shot 方式。

## 决策

第一版为每个受支持的操作系统与 CPU 架构发布一个归档，其中包含：

- Node SEA TUI 可执行文件；
- Python Server 的平台可执行文件；
- `release-manifest.json`、SHA-256 校验文件和 SBOM；
- 安装、诊断与许可证信息。

Node 只从自身受控的发布目录定位 Python Server；开发模式仍可使用 `uv run`。Python Server
另行发布 wheel，提供 `py-hermes` 的 headless/one-shot 用法，但交互 TUI 不依赖用户机器上的
Python 或该 wheel。

## 备选方案与取舍

1. 系统 Python：包体最小，但解释器/依赖差异会破坏全新环境可复现性，且离线安装较差。
2. 内置隔离 Python 环境：保留解释器语义，但运行时复制、升级和签名边界复杂，产物仍为平台相关。
3. Python Server 平台可执行文件：提供稳定的单入口和离线体验；代价是需要跨平台构建、签名和较大产物。
4. 分别安装 Node 与 Python 包：适合开发者与 headless 用户，但不能保证交互模式的运行时组合。

选择方案 3 作为交互式发布主路径，并以方案 4 补充 headless 使用场景。

## 版本兼容契约

`hermes/release_manifest.json` 是源码内的版本事实来源，包含发布格式、发布版本、Node 包版本、
Python 包版本和 Protocol 版本。构建产物必须复制该清单；Node bundle 与 Python Server 在启动握手
中核验 `release_version`、`python_package_version` 和 `protocol_version`。任一不一致均必须在 TUI
进入交互状态前失败，并说明应重新安装同一发布版本或回滚。

Protocol 的向后兼容性仍由 `buf breaking` 保护；版本号变更不能替代 schema 兼容性检查。

## 后果

- 首版支持矩阵将显式限定目标系统和架构，而非声称任意 Python/Node 环境均可运行。
- Python Server 的启动命令需从“解释器加 `-m`”演进为可表达平台二进制的受控命令对象。
- 发布 CI 必须在每个目标上生成校验信息、SBOM，并执行打包后的启动 smoke test。
