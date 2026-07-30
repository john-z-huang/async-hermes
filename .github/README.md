# GitHub 仓库治理配置

## 开放 Issue 关联校验

工作流 `workflows/require-open-issue.yml` 负责检查代码变更是否遵循项目流程：

- Pull Request 必须使用 `Closes #编号`、`Fixes #编号` 或 `Resolves #编号`，关联当前仓库至少一个开放 Issue。
- 只关联已关闭 Issue、其他仓库 Issue 或使用普通文本引用时，检查失败。
- 直接推送到 `main` 会产生失败检查，并提示改用 Pull Request。

工作流使用 `pull_request_target` 读取 GitHub 生成的 Issue 关联关系，不检出或执行
Pull Request 中的代码。其权限仅限读取仓库内容、Pull Request 和 Issue。

## 必需的仓库规则

GitHub Actions 在 push 已被服务器接收后才开始运行，因此失败工作流本身无法撤销或
真正阻止一次直接推送。仓库使用 `ruleset-main.json` 记录 `main` 的 Ruleset 配置：

1. 当前启用“所有改动必须通过 Pull Request 合并”，用于拒绝直接 push；
2. 将 `校验开放 Issue 关联` 设置为必需状态检查；
3. 禁止绕过上述规则，或只向明确的维护者开放紧急绕过权限；
4. 在本工作流首次合并到默认分支并成功运行后，再启用必需状态检查，避免初始化时
   因检查尚不存在而阻塞首次合并。

这样，Ruleset 负责在接收更新前阻止绕过 PR 的写入，GitHub Actions 负责验证 PR
关联的 Issue 是否存在且保持开放。
