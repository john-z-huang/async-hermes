#!/usr/bin/env bash
# 校验 AGENTS.md 对 dev-workflow-standards Skill 的描述是否与 Skill 最新 commit 同步。
# 用法：bash scripts/check-dev-workflow-skill.sh
# 已同步返回 0；落后返回 1。远端不可校验时仅提示，不因此判为落后。
#
# 可覆盖的环境变量（便于测试）：
#   DEV_WORKFLOW_SKILL_DIR        Skill 本地目录，默认 $HOME/.agents/skills/dev-workflow-standards
#   DEV_WORKFLOW_RECORDED_FILE    记录文件路径，默认 <repo>/docs/dev-workflow-standards.commit
#   DEV_WORKFLOW_REMOTE_HEAD      远端 main 的 commit，默认通过 git ls-remote 获取
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
recorded_file="${DEV_WORKFLOW_RECORDED_FILE:-$repo_root/docs/dev-workflow-standards.commit}"
skill_dir="${DEV_WORKFLOW_SKILL_DIR:-$HOME/.agents/skills/dev-workflow-standards}"

if [[ ! -f "$recorded_file" ]]; then
  echo "[skill-sync] 缺少版本记录文件：$recorded_file" >&2
  exit 1
fi
recorded="$(tr -d '[:space:]' < "$recorded_file")"

if [[ ! -d "$skill_dir/.git" ]]; then
  echo "[skill-sync] 未找到本地 Skill：$skill_dir" >&2
  exit 1
fi

local_head="$(git -C "$skill_dir" rev-parse HEAD 2>/dev/null || true)"
remote_head="${DEV_WORKFLOW_REMOTE_HEAD:-$(git -C "$skill_dir" ls-remote origin main 2>/dev/null | awk 'NR==1{print $1}' || true)}"

echo "[skill-sync] 记录版本: ${recorded:0:12}"
echo "[skill-sync] 本地 HEAD: ${local_head:0:12}"
echo "[skill-sync] 远端 main: ${remote_head:0:12}"

if [[ -z "$local_head" ]]; then
  echo "[skill-sync] 无法读取本地 Skill HEAD，跳过本地比较" >&2
fi
if [[ -z "$remote_head" ]]; then
  echo "[skill-sync] 无法读取远端 main（可能无网络），跳过远端比较" >&2
fi

stale=0
[[ -n "$local_head" && "$recorded" != "$local_head" ]] && stale=1
[[ -n "$remote_head" && "$recorded" != "$remote_head" ]] && stale=1

if [[ "$stale" == 1 ]]; then
  echo "[skill-sync] 落后：AGENTS.md 中对 dev-workflow-standards 的描述可能已过期。" >&2
  echo "[skill-sync] 请阅读 Skill 的 SKILL.md，将新增的相关约定补充到 AGENTS.md，并把 $recorded_file 更新为 Skill 最新 commit。" >&2
  exit 1
fi

echo "[skill-sync] 已同步。"
exit 0
