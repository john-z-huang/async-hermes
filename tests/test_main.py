"""Tests for the Code Agent's default read-only shell capability."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main
from openai.types.responses import (
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseTextDeltaEvent,
)


def make_shell_request(*command: str) -> SimpleNamespace:
    """Create the portion of an SDK shell request used by the executor."""
    return SimpleNamespace(
        data=SimpleNamespace(action=SimpleNamespace(commands=list(command)))
    )


def make_stream_result(
    *events: object,
    final_output: str = "done",
) -> SimpleNamespace:
    """Create the portion of a streamed SDK result used by the runner tests."""

    async def stream_events():
        for event in events:
            yield event

    return SimpleNamespace(
        final_output=final_output,
        stream_events=stream_events,
    )


def make_raw_response_event(data: object) -> SimpleNamespace:
    """Wrap an OpenAI response event as an Agents SDK raw stream event."""
    return SimpleNamespace(type="raw_response_event", data=data)


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

        def fake_run(agent: object, task_input: str) -> SimpleNamespace:
            captured["agent"] = agent
            captured["task_input"] = task_input
            return make_stream_result()

        with patch.object(main.Runner, "run_streamed", side_effect=fake_run):
            output = await main.run_code_agent("List the project files")

        self.assertEqual(output, "done")
        agent = captured["agent"]
        self.assertTrue(any(tool.name == "shell" for tool in agent.tools))
        self.assertEqual(agent.model_settings.reasoning.effort, "none")

    async def test_enables_default_reasoning_effect_when_requested(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(agent: object, task_input: str) -> SimpleNamespace:
            captured["agent"] = agent
            return make_stream_result()

        with patch.object(main.Runner, "run_streamed", side_effect=fake_run):
            await main.run_code_agent("Think carefully", enable_reasoning=True)

        agent = captured["agent"]
        self.assertEqual(agent.model_settings.reasoning.effort, "medium")

    async def test_uses_requested_reasoning_effect(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(agent: object, task_input: str) -> SimpleNamespace:
            captured["agent"] = agent
            return make_stream_result()

        with patch.object(main.Runner, "run_streamed", side_effect=fake_run):
            await main.run_code_agent(
                "Think very carefully",
                enable_reasoning=True,
                reason_effect="high",
            )

        agent = captured["agent"]
        self.assertEqual(agent.model_settings.reasoning.effort, "high")

    async def test_ignores_reason_effect_when_reasoning_is_disabled(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(agent: object, task_input: str) -> SimpleNamespace:
            captured["agent"] = agent
            return make_stream_result()

        with patch.object(main.Runner, "run_streamed", side_effect=fake_run):
            await main.run_code_agent(
                "Do not reason",
                enable_reasoning=False,
                reason_effect="unsupported",
            )

        agent = captured["agent"]
        self.assertEqual(agent.model_settings.reasoning.effort, "none")

    async def test_streams_content_deltas_and_returns_final_output(self) -> None:
        stream = StringIO()
        result = make_stream_result(
            make_raw_response_event(
                ResponseTextDeltaEvent.model_construct(delta="hel")
            ),
            make_raw_response_event(
                ResponseTextDeltaEvent.model_construct(delta="lo")
            ),
            final_output="hello",
        )

        with patch.object(main.Runner, "run_streamed", return_value=result):
            output = await main.run_code_agent("Say hello", output_stream=stream)

        self.assertEqual(output, "hello")
        self.assertEqual(stream.getvalue(), "hello\n")

    async def test_labels_reasoning_and_content_streams(self) -> None:
        stream = StringIO()
        result = make_stream_result(
            make_raw_response_event(
                ResponseReasoningSummaryTextDeltaEvent.model_construct(
                    delta="thinking"
                )
            ),
            make_raw_response_event(
                ResponseTextDeltaEvent.model_construct(delta="answer")
            ),
            final_output="answer",
        )

        with patch.object(main.Runner, "run_streamed", return_value=result):
            await main.run_code_agent(
                "Think",
                enable_reasoning=True,
                output_stream=stream,
            )

        self.assertEqual(
            stream.getvalue(),
            "[reasoning]\nthinking\n[content]\nanswer\n",
        )

    async def test_does_not_distinguish_streams_when_reasoning_is_disabled(
        self,
    ) -> None:
        stream = StringIO()
        result = make_stream_result(
            make_raw_response_event(
                ResponseReasoningSummaryTextDeltaEvent.model_construct(
                    delta="hidden"
                )
            ),
            make_raw_response_event(
                ResponseTextDeltaEvent.model_construct(delta="answer")
            ),
        )

        with patch.object(main.Runner, "run_streamed", return_value=result):
            await main.run_code_agent("Answer", output_stream=stream)

        self.assertEqual(stream.getvalue(), "hiddenanswer\n")

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

        self.assertTrue(args.enable_stream_output)
        self.assertFalse(args.enable_reasoning)
        self.assertEqual(args.reason_effect, "medium")
        self.assertEqual(args.workspace, Path("/tmp").resolve())
        self.assertEqual(args.permissions, "read-only")

    def test_accepts_disabled_stream_output(self) -> None:
        args = main.parse_args(
            [
                "--question",
                "test",
                "--enable-stream-output",
                "false",
            ]
        )

        self.assertFalse(args.enable_stream_output)

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


class MainTests(unittest.TestCase):
    def test_streams_to_stdout_by_default_without_duplicate_print(self) -> None:
        stdout = StringIO()

        with (
            patch("main.run_code_agent", new_callable=AsyncMock) as run_code_agent,
            redirect_stdout(stdout),
        ):
            main.main(["--question", "test"])

        self.assertEqual(stdout.getvalue(), "")
        self.assertIs(run_code_agent.await_args.kwargs["output_stream"], stdout)

    def test_prints_complete_output_when_streaming_is_disabled(self) -> None:
        stdout = StringIO()

        with (
            patch(
                "main.run_code_agent",
                new_callable=AsyncMock,
                return_value="complete response",
            ) as run_code_agent,
            redirect_stdout(stdout),
        ):
            main.main(
                [
                    "--question",
                    "test",
                    "--enable-stream-output",
                    "false",
                ]
            )

        self.assertEqual(stdout.getvalue(), "complete response\n")
        self.assertIsNone(run_code_agent.await_args.kwargs["output_stream"])
