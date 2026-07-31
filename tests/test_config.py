from __future__ import annotations

from pathlib import Path

import pytest

from hermes.config import ConfigError, default_config_path, load_config
from hermes.interfaces import cli


def test_checked_in_example_is_a_valid_shared_contract() -> None:
    loaded = load_config(Path("hermes.config.example.toml"))
    assert loaded.rpc.host == "127.0.0.1"
    assert loaded.rpc.port == 50051
    assert loaded.agent.permissions == "read-only"
    assert loaded.tui.show_reasoning is False


def write_config(path: Path, body: str) -> Path:
    config = path / "hermes.config.toml"
    config.write_text(body, encoding="utf-8")
    return config


def test_loads_shared_toml_config_and_resolves_workspace_from_config(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        """# TOML 注释必须可用。
        version = 1

        [rpc]
        host = "127.0.0.1"
        port = 50051

        [agent]
        workspace = "."
        permissions = "read-only"
        enableReasoning = true
        reasonEffect = "high"

        [tui]
        showReasoning = true
        """,
    )
    loaded = load_config(config)
    assert loaded.rpc.port == 50051
    assert loaded.rpc.startup_timeout_ms == 10000
    assert loaded.agent.workspace == tmp_path.resolve()
    assert loaded.agent.enable_reasoning is True
    assert loaded.tui.show_reasoning is True


@pytest.mark.parametrize(
    "body, message",
    [
        ('version = 2\n[rpc]\nhost = "127.0.0.1"\nport = 1', "版本"),
        ('version = 1\n[rpc]\nhost = "0.0.0.0"\nport = 1', "loopback"),
        ('version = 1\n[rpc]\nhost = "127.0.0.1"\nport = "1"', "port"),
        ('version = 1\nsecret = "no"\n[rpc]\nhost = "127.0.0.1"\nport = 1', "未知字段"),
        ('version = 1\n[rpc]\nhost = "127.0.0.1"\nport =', "无法解析 TOML"),
    ],
)
def test_rejects_invalid_or_unsafe_config(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, body))


def test_cli_config_values_are_defaults_and_cli_explicit_value_wins(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        """version = 1
        [rpc]
        host = "127.0.0.1"
        port = 50051
        [agent]
        workspace = "."
        enableReasoning = true
        reasonEffect = "high"
        """,
    )
    args = cli.parse_args(["--config", str(config)])
    assert args.workspace == tmp_path.resolve()
    assert args.enable_reasoning is True
    assert args.reason_effect == "high"
    overridden = cli.parse_args(["--config", str(config), "--enable-reasoning", "false"])
    assert overridden.enable_reasoning is False


def test_rejects_json5_config_with_migration_guidance(tmp_path: Path) -> None:
    config = tmp_path / "hermes.config.json5"
    config.write_text("{ version: 1 }", encoding="utf-8")
    with pytest.raises(ConfigError, match="已不再支持"):
        load_config(config)


def test_rejects_missing_toml_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="已存在的普通文件"):
        load_config(tmp_path / "missing.toml")


def test_loads_user_default_config_outside_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_directory = tmp_path / ".async-hermes"
    config_directory.mkdir()
    config = config_directory / "config.toml"
    config.write_text('version = 1\n[rpc]\nhost = "127.0.0.1"\nport = 50051', encoding="utf-8")
    assert default_config_path(tmp_path) == config

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(cli, "default_config_path", lambda: config)
    args = cli.parse_args(["--workspace", str(workspace)])

    assert args.config == config
    assert args.workspace == workspace
