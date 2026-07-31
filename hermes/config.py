"""非敏感 Hermes 运行配置的加载与校验。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from hermes.infrastructure.agents_sdk_runner import REASON_EFFECTS
from hermes.infrastructure.workspace_tools import PERMISSION_PROFILES, resolve_workspace


CONFIG_VERSION = 1
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class ConfigError(ValueError):
    """配置文件无法安全用于启动时抛出。"""


@dataclass(frozen=True, slots=True)
class RpcConfig:
    host: str
    port: int
    startup_timeout_ms: int


@dataclass(frozen=True, slots=True)
class AgentConfig:
    workspace: Path | None = None
    permissions: str | None = None
    enable_reasoning: bool | None = None
    reason_effect: str | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    content: str | None = None


@dataclass(frozen=True, slots=True)
class TuiConfig:
    show_reasoning: bool = False


@dataclass(frozen=True, slots=True)
class HermesConfig:
    path: Path
    rpc: RpcConfig
    agent: AgentConfig
    tui: TuiConfig


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} 必须是对象。")
    return value


def _unknown(values: dict[str, Any], allowed: set[str], field: str) -> None:
    unexpected = sorted(set(values) - allowed)
    if unexpected:
        raise ConfigError(f"{field} 包含未知字段：{', '.join(unexpected)}。")


def _string(values: dict[str, Any], field: str, *, required: bool = False) -> str | None:
    key = field.rsplit(".", 1)[-1]
    if key not in values and not required:
        return None
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} 必须是非空字符串。")
    return value


def _boolean(values: dict[str, Any], field: str) -> bool | None:
    key = field.rsplit(".", 1)[-1]
    if key not in values:
        return None
    value = values.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{field} 必须是布尔值。")
    return value


def load_config(value: str | Path) -> HermesConfig:
    """加载显式指定的 TOML 文件；相对 workspace 路径以配置文件为基准。"""
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"配置文件必须是已存在的普通文件：{path}")
    if path.suffix.casefold() == ".json5":
        raise ConfigError(
            f"JSON5 配置文件已不再支持：{path}。请将其迁移为 TOML，并使用 hermes.config.example.toml 作为示例。"
        )
    try:
        with path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"无法解析 TOML 配置文件 {path}：{error}") from error
    root = _mapping(raw, "配置根")
    _unknown(root, {"version", "rpc", "agent", "tui"}, "配置根")
    if root.get("version") != CONFIG_VERSION:
        raise ConfigError(f"仅支持配置版本 {CONFIG_VERSION}。")

    rpc_values = _mapping(root.get("rpc"), "rpc")
    _unknown(rpc_values, {"host", "port", "startupTimeoutMs"}, "rpc")
    host = _string(rpc_values, "rpc.host", required=True)
    if host not in LOOPBACK_HOSTS:
        raise ConfigError("rpc.host 只能是 loopback 地址。")
    port = rpc_values.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ConfigError("rpc.port 必须是 0 到 65535 的整数。")
    timeout = rpc_values.get("startupTimeoutMs", 10000)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ConfigError("rpc.startupTimeoutMs 必须是正整数。")

    agent_values = _mapping(root.get("agent", {}), "agent")
    _unknown(agent_values, {"workspace", "permissions", "enableReasoning", "reasonEffect", "systemPrompt", "userPrompt", "content"}, "agent")
    workspace_value = _string(agent_values, "agent.workspace")
    workspace = None
    if workspace_value is not None:
        workspace = resolve_workspace(path.parent / workspace_value)
    permissions = _string(agent_values, "agent.permissions")
    if permissions is not None and permissions not in PERMISSION_PROFILES:
        raise ConfigError(f"agent.permissions 不受支持：{permissions}。")
    reason_effect = _string(agent_values, "agent.reasonEffect")
    if reason_effect is not None and reason_effect not in REASON_EFFECTS:
        raise ConfigError(f"agent.reasonEffect 不受支持：{reason_effect}。")

    tui_values = _mapping(root.get("tui", {}), "tui")
    _unknown(tui_values, {"showReasoning"}, "tui")
    show_reasoning = _boolean(tui_values, "tui.showReasoning")
    return HermesConfig(
        path=path,
        rpc=RpcConfig(host=host, port=port, startup_timeout_ms=timeout),
        agent=AgentConfig(
            workspace=workspace,
            permissions=permissions,
            enable_reasoning=_boolean(agent_values, "agent.enableReasoning"),
            reason_effect=reason_effect,
            system_prompt=_string(agent_values, "agent.systemPrompt"),
            user_prompt=_string(agent_values, "agent.userPrompt"),
            content=_string(agent_values, "agent.content"),
        ),
        tui=TuiConfig(show_reasoning=show_reasoning if show_reasoning is not None else False),
    )
