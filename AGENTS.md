# Agent 协作指南

## 项目背景与目标

Hermes 是一个基于 Python 的 Code Agent 原型，研究如何将材料密集型任务中的大规模原始内容处理，与 Agent 的主推理过程解耦。

当任务包含大量代码、文档或其他原始材料时，主 Agent 不应将材料全文直接读入上下文。应先识别材料的结构和处理目标，构造提示词与 JSONL（每行一个请求的 JSON 文件）输入，再通过异步 Batch API 批量处理。主上下文只保留任务计划、材料结构、提示词和经校验的结果摘要，从而降低 Token 消耗、控制成本，并提高长任务的可持续性。

所有变更都应与 [docs/GOAL.md](docs/GOAL.md) 保持一致。该项目当前的最小可行原型用于验证这一工作流，不应在缺少明确需求时扩展为无关的产品功能。

## 最小可行原型工作流

处理材料密集型任务时，按以下顺序工作：

1. 识别任务目标、输入材料类型、材料边界和所需输出结构。
2. 为材料处理生成适配的提示词；将材料拆分、整理为 JSONL 请求，避免把大量原始内容放入主 Agent 上下文。
3. 异步提交 Batch API 请求，并记录可追踪的请求与输出位置。
4. 回收批处理结果，校验其完整性、格式和质量，汇总为精简且可追溯的信息。
5. 仅基于任务计划、必要元数据和结果摘要继续决策或执行；需要原始材料时，按需、局部读取。

## 执行边界与技术约束

- 不将大量原始材料直接读入主上下文；优先使用材料清单、结构摘要、局部检索和异步批处理。
- 使用 `uv` 管理依赖，运行环境保持 Python 3.13 及以上；未经明确需求，不修改依赖、Python 版本或运行时架构。
- `.agents/` 仅存放运行时生成的 Agent 响应产物，不作为长期源码或文档修改目标，也不应提交为产品资产。
- 修改应用逻辑、提示词、数据格式或工作流前，确认其与 [docs/GOAL.md](docs/GOAL.md) 的研究目标一致；若会改变材料边界、成本模型或结果质量要求，先说明影响并取得确认。

## 项目目录

- `main.py`：当前应用入口，包含 Agent 运行、交互循环和 workspace 工具集成。
- `tests/`：自动化测试；当前核心测试位于 `tests/test_main.py`。
- `docs/`：项目目标与设计文档；`docs/GOAL.md` 是目标与工作流的权威说明。
- `.agents/`：运行时生成的 Agent 响应产物，不应作为源代码或长期文档修改目标。
- `pyproject.toml` 与 `uv.lock`：项目元数据、依赖声明和锁定版本。
- `README.md`：用户使用说明，包括 workspace 检查工具和运行模式。

## 通用开发工作流规范

本项目的通用开发工作流规范由 Skill `dev-workflow-standards` 统一管理，本文件不重复这些通用内容。该 Skill 本地位于 `~/.agents/skills/dev-workflow-standards`，仓库为 `https://github.com/john-z-huang/dev-workflow-standards`。涉及以下事项时，遵循该 Skill 的约定：

- Git 分支命名、提交规范与禁止 Code Agent 署名
- 文档与提交语言（使用中文）
- GitHub 操作规范、网络与认证、`GH_TOKEN` 安全处理
- Issue 与 PR 流程、PR 合并后清理
- 项目管理与快速检查清单

项目级入口与看板：

- 目标与工作流权威说明：[docs/GOAL.md](docs/GOAL.md)
- 规划看板：[Hermes 架构演进 Roadmap](https://github.com/users/john-z-huang/projects/7/views/1)

当需要优化通用开发工作流时，修改 `dev-workflow-standards` Skill 并推送到其 GitHub 仓库，不在本文件中追加通用开发流程内容。
