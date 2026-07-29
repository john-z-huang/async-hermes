"""Tests for the Code Agent's default read-only shell capability."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


def make_shell_request(*command: str) -> SimpleNamespace:
    """Create the portion of an SDK shell request used by the executor."""
    return SimpleNamespace(
        data=SimpleNamespace(action=SimpleNamespace(command=list(command)))
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
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(main._validate_read_only_command(command))

    def test_rejects_paths_outside_the_project(self) -> None:
        self.assertIsNotNone(main._validate_read_only_command(("cat", "/etc/passwd")))
        self.assertIsNotNone(main._validate_read_only_command(("cat", "../secret.txt")))


class ReadOnlyShellExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_an_allowed_command_in_the_project_root(self) -> None:
        output = await main.execute_read_only_shell(make_shell_request("pwd"))

        self.assertEqual(output.strip(), str(main.PROJECT_ROOT))

    async def test_rejects_command_without_starting_a_process(self) -> None:
        with patch("main.asyncio.create_subprocess_exec", new_callable=AsyncMock) as execute:
            output = await main.execute_read_only_shell(
                make_shell_request("rm", "important-file")
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
        self.assertTrue(any(tool.name == "local_shell" for tool in agent.tools))
