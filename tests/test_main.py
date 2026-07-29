"""Tests for the Code Agent's default read-only shell capability."""

from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


def make_shell_request(*command: str) -> SimpleNamespace:
    """Create the portion of an SDK shell request used by the executor."""
    return SimpleNamespace(
        data=SimpleNamespace(action=SimpleNamespace(commands=list(command)))
    )


class ValidateReadOnlyCommandTests(unittest.TestCase):
    def test_allows_read_only_commands(self) -> None:
        for command in (
            ("pwd",),
            ("rg", "--files"),
            ("git", "status", "--short"),
            ("find", ".", "-maxdepth", "1"),
        ):
            with self.subTest(command=command):
                self.assertIsNone(main._validate_read_only_command(command))

    def test_rejects_unknown_or_mutating_commands(self) -> None:
        for command in (
            (),
            ("rm", "file.txt"),
            ("git", "commit", "-m", "message"),
            ("find", ".", "-delete"),
            ("rg", "--pre", "formatter", "pattern"),
            ("git", "--git-dir=/tmp/other", "status"),
            ("pwd", "&&", "ls"),
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(main._validate_read_only_command(command))

    def test_rejects_paths_outside_the_project(self) -> None:
        self.assertIsNotNone(main._validate_read_only_command(("cat", "/etc/passwd")))
        self.assertIsNotNone(main._validate_read_only_command(("cat", "../secret.txt")))

    def test_uses_the_given_workspace_as_the_path_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            inside = workspace / "inside.txt"
            inside.touch()

            self.assertIsNone(
                main._validate_read_only_command(
                    ("cat", str(inside)),
                    workspace=workspace,
                )
            )
            self.assertIsNotNone(
                main._validate_read_only_command(
                    ("cat", str(Path(__file__).resolve())),
                    workspace=workspace,
                )
            )

    def test_rejects_a_symlink_that_escapes_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / "outside").symlink_to(Path(__file__).resolve())

            self.assertIsNotNone(
                main._validate_read_only_command(
                    ("cat", "outside"),
                    workspace=workspace,
                )
            )


class ReadOnlyShellExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_an_allowed_command_in_the_current_directory(self) -> None:
        output = await main.execute_read_only_shell(make_shell_request("pwd"))

        self.assertEqual(output.strip(), str(Path.cwd().resolve()))

    async def test_executes_commands_in_the_given_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / "example.txt").touch()
            output = await main.execute_read_only_shell(
                make_shell_request("pwd && ls -1p"),
                workspace=workspace,
            )

        self.assertIn(str(workspace.resolve()), output)
        self.assertIn("example.txt", output)

    async def test_rejects_command_without_starting_a_process(self) -> None:
        with patch("main.asyncio.create_subprocess_exec", new_callable=AsyncMock) as execute:
            output = await main.execute_read_only_shell(
                make_shell_request("rm important-file")
            )

        self.assertIn("不在只读命令白名单", output)
        execute.assert_not_awaited()


class RunCodeAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_registers_read_only_shell_tool(self) -> None:
        captured: dict[str, object] = {}

        async def fake_run(agent: object, task_input: str) -> SimpleNamespace:
            captured["agent"] = agent
            captured["task_input"] = task_input
            return SimpleNamespace(final_output="done")

        with patch.object(main.Runner, "run", new=AsyncMock(side_effect=fake_run)):
            output = await main.run_code_agent("List the project files")

        self.assertEqual(output, "done")
        agent = captured["agent"]
        self.assertTrue(any(tool.name == "shell" for tool in agent.tools))
        self.assertEqual(agent.model_settings.reasoning.effort, "none")

    async def test_enables_default_reasoning_effect_when_requested(self) -> None:
        captured: dict[str, object] = {}

        async def fake_run(agent: object, task_input: str) -> SimpleNamespace:
            captured["agent"] = agent
            return SimpleNamespace(final_output="done")

        with patch.object(main.Runner, "run", new=AsyncMock(side_effect=fake_run)):
            await main.run_code_agent("Think carefully", enable_reasoning=True)

        agent = captured["agent"]
        self.assertEqual(agent.model_settings.reasoning.effort, "medium")

    async def test_uses_requested_reasoning_effect(self) -> None:
        captured: dict[str, object] = {}

        async def fake_run(agent: object, task_input: str) -> SimpleNamespace:
            captured["agent"] = agent
            return SimpleNamespace(final_output="done")

        with patch.object(main.Runner, "run", new=AsyncMock(side_effect=fake_run)):
            await main.run_code_agent(
                "Think very carefully",
                enable_reasoning=True,
                reason_effect="high",
            )

        agent = captured["agent"]
        self.assertEqual(agent.model_settings.reasoning.effort, "high")

    async def test_ignores_reason_effect_when_reasoning_is_disabled(self) -> None:
        captured: dict[str, object] = {}

        async def fake_run(agent: object, task_input: str) -> SimpleNamespace:
            captured["agent"] = agent
            return SimpleNamespace(final_output="done")

        with patch.object(main.Runner, "run", new=AsyncMock(side_effect=fake_run)):
            await main.run_code_agent(
                "Do not reason",
                enable_reasoning=False,
                reason_effect="unsupported",
            )

        agent = captured["agent"]
        self.assertEqual(agent.model_settings.reasoning.effort, "none")

    async def test_rejects_unknown_effect_when_reasoning_is_enabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知 reason-effect"):
            await main.run_code_agent(
                "Think",
                enable_reasoning=True,
                reason_effect="unsupported",
            )


class ParseArgsTests(unittest.TestCase):
    def test_new_arguments_use_expected_defaults(self) -> None:
        with patch("main.Path.cwd", return_value=Path("/tmp")):
            args = main.parse_args(["--question", "test"])

        self.assertFalse(args.enable_reasoning)
        self.assertEqual(args.reason_effect, "medium")
        self.assertEqual(args.workspace, Path("/tmp").resolve())
        self.assertEqual(args.permissions, "read-only")

    def test_accepts_explicit_reasoning_effect_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            args = main.parse_args(
                [
                    "--question",
                    "test",
                    "--enable-reasoning",
                    "true",
                    "--reason-effect",
                    "high",
                    "--workspace",
                    temporary_directory,
                    "--permissions",
                    "read-only",
                ]
            )

        self.assertTrue(args.enable_reasoning)
        self.assertEqual(args.reason_effect, "high")
        self.assertEqual(args.workspace, Path(temporary_directory).resolve())

    def test_ignores_unknown_effect_when_reasoning_is_disabled(self) -> None:
        args = main.parse_args(
            [
                "--question",
                "test",
                "--enable-reasoning",
                "false",
                "--reason-effect",
                "unsupported",
            ]
        )

        self.assertFalse(args.enable_reasoning)
        self.assertEqual(args.reason_effect, "unsupported")

    def test_rejects_unknown_effect_when_reasoning_is_enabled(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                main.parse_args(
                    [
                        "--question",
                        "test",
                        "--enable-reasoning",
                        "true",
                        "--reason-effect",
                        "unsupported",
                    ]
                )
