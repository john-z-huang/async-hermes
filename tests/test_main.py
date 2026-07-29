"""Tests for the Code Agent's default read-only shell capability."""

from __future__ import annotations

from contextlib import redirect_stderr
import hashlib
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main
import pytest
from agents import FunctionTool
from agents.tool_context import ToolContext
from openai.types.responses import (
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseTextDeltaEvent,
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
        output = await main.execute_read_only_shell(["pwd"])

        self.assertEqual(output.strip(), str(Path.cwd().resolve()))

    async def test_executes_commands_in_the_given_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / "example.txt").touch()
            output = await main.execute_read_only_shell(
                ["pwd && ls -1p"],
                workspace=workspace,
            )

        self.assertIn(str(workspace.resolve()), output)
        self.assertIn("example.txt", output)

    async def test_rejects_command_without_starting_a_process(self) -> None:
        with patch("main.asyncio.create_subprocess_exec", new_callable=AsyncMock) as execute:
            output = await main.execute_read_only_shell(
                ["rm important-file"]
            )

        self.assertIn("不在只读命令白名单", output)
        execute.assert_not_awaited()


class ReadOnlyWorkspaceFunctionToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_exposes_a_strict_function_call_schema(self) -> None:
        tool = main.build_read_only_workspace_tool()

        self.assertIsInstance(tool, FunctionTool)
        self.assertEqual(tool.name, "inspect_workspace")
        self.assertTrue(tool.strict_json_schema)
        self.assertEqual(tool.params_json_schema["required"], ["commands"])
        self.assertFalse(tool.params_json_schema["additionalProperties"])
        self.assertEqual(
            tool.params_json_schema["properties"]["commands"]["items"]["type"],
            "string",
        )
        self.assertIn(
            "read-only",
            tool.params_json_schema["properties"]["commands"]["description"],
        )

    async def test_invokes_the_shared_read_only_executor(self) -> None:
        tool = main.build_read_only_workspace_tool()
        arguments = '{"commands":["pwd","ls -1p"]}'
        context = ToolContext(
            context=None,
            tool_name=tool.name,
            tool_call_id="call-1",
            tool_arguments=arguments,
        )

        with patch(
            "main.execute_read_only_shell",
            new_callable=AsyncMock,
            return_value="project files",
        ) as execute:
            output = await tool.on_invoke_tool(
                context,
                arguments,
            )

        self.assertEqual(output, "project files")
        execute.assert_awaited_once_with(
            ["pwd", "ls -1p"],
            workspace=Path.cwd().resolve(),
            permissions=main.DEFAULT_AGENT_PERMISSIONS,
        )

    async def test_does_not_parse_tool_tags_from_model_text(self) -> None:
        stream = StringIO()
        text = '<tool_call>{"commands":["pwd"]}</tool_call>'
        result = make_stream_result(
            make_raw_response_event(
                ResponseTextDeltaEvent.model_construct(delta=text)
            ),
            final_output=text,
        )

        with (
            patch.object(main.Runner, "run_streamed", return_value=result),
            patch(
                "main.execute_read_only_shell",
                new_callable=AsyncMock,
            ) as execute,
        ):
            output = await main.run_code_agent("Inspect", output_stream=stream)

        self.assertEqual(output, text)
        self.assertEqual(stream.getvalue(), f"{text}\n")
        execute.assert_not_awaited()


class RunCodeAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_registers_structured_read_only_function_tool(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(agent: object, task_input: str) -> SimpleNamespace:
            captured["agent"] = agent
            captured["task_input"] = task_input
            return make_stream_result()

        with patch.object(main.Runner, "run_streamed", side_effect=fake_run):
            output = await main.run_code_agent("List the project files")

        self.assertEqual(output, "done")
        agent = captured["agent"]
        self.assertEqual([tool.name for tool in agent.tools], ["inspect_workspace"])
        self.assertIsInstance(agent.tools[0], FunctionTool)
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
                ResponseFunctionCallArgumentsDeltaEvent.model_construct(
                    delta='{"commands":["pwd"]}'
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

    async def test_captures_reasoning_and_content_without_console_stream(
        self,
    ) -> None:
        captured_response = StringIO()
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
            output = await main.run_code_agent(
                "Think",
                enable_reasoning=True,
                output_stream=None,
                capture_stream=captured_response,
            )

        self.assertEqual(output, "answer")
        self.assertEqual(
            captured_response.getvalue(),
            "[reasoning]\nthinking\n[content]\nanswer\n",
        )

    async def test_hides_reasoning_stream_when_reasoning_is_disabled(
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

        self.assertEqual(stream.getvalue(), "answer\n")

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
        self.assertIsNone(args.output_file)
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

    def test_accepts_custom_output_file(self) -> None:
        args = main.parse_args(
            [
                "--question",
                "test",
                "--output-file",
                "results/answer.txt",
            ]
        )

        self.assertEqual(args.output_file, "results/answer.txt")

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


class TestPersistAgentOutput:
    def test_uses_hashed_default_name_under_agents_directory(
        self,
        tmp_path: Path,
    ) -> None:
        output = "完整响应"
        expected_hash = hashlib.sha256(output.encode("utf-8")).hexdigest()[:16]
        output_path = main.persist_agent_output(output, workspace=tmp_path)

        assert output_path == (
            tmp_path / ".agents" / f"response-{expected_hash}.txt"
        )
        assert output_path.read_text(encoding="utf-8") == output

    def test_writes_custom_relative_path_inside_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        output_path = main.persist_agent_output(
            "complete response",
            workspace=tmp_path,
            output_file="results/answer.txt",
        )

        assert output_path == tmp_path / "results" / "answer.txt"
        assert output_path.read_text(encoding="utf-8") == "complete response"

    @pytest.mark.parametrize(
        "output_file",
        ["/tmp/answer.txt", "../answer.txt", "."],
    )
    def test_rejects_output_paths_outside_workspace(
        self,
        tmp_path: Path,
        output_file: str,
    ) -> None:
        with pytest.raises(ValueError, match="workspace"):
            main.persist_agent_output(
                "response",
                workspace=tmp_path,
                output_file=output_file,
            )

    def test_rejects_symlink_that_escapes_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "workspace"
        outside_directory = tmp_path / "outside"
        workspace.mkdir()
        outside_directory.mkdir()
        (workspace / "outside").symlink_to(outside_directory)

        with pytest.raises(ValueError, match="workspace"):
            main.persist_agent_output(
                "response",
                workspace=workspace,
                output_file="outside/answer.txt",
            )


class TestMain:
    def test_streams_to_stdout_and_prints_default_output_path(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output = "complete response"
        expected_hash = hashlib.sha256(output.encode("utf-8")).hexdigest()[:16]
        expected_path = (
            tmp_path / ".agents" / f"response-{expected_hash}.txt"
        )
        run_code_agent = AsyncMock(return_value=output)

        with patch("main.run_code_agent", run_code_agent):
            main.main(
                [
                    "--question",
                    "test",
                    "--workspace",
                    str(tmp_path),
                ]
            )

        assert capsys.readouterr().out == f"响应结果已保存到：{expected_path}\n"
        assert expected_path.read_text(encoding="utf-8") == output
        assert run_code_agent.await_args.kwargs["output_stream"] is not None

    def test_prints_complete_output_and_custom_path_when_streaming_is_disabled(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        expected_path = tmp_path / "results" / "answer.txt"

        async def fake_run_code_agent(**kwargs: object) -> str:
            capture_stream = kwargs["capture_stream"]
            capture_stream.write(
                "[reasoning]\ncareful analysis\n[content]\ncomplete response\n"
            )
            return "complete response"

        run_code_agent = AsyncMock(side_effect=fake_run_code_agent)

        with patch("main.run_code_agent", run_code_agent):
            main.main(
                [
                    "--question",
                    "test",
                    "--enable-stream-output",
                    "false",
                    "--workspace",
                    str(tmp_path),
                    "--output-file",
                    "results/answer.txt",
                ]
            )

        assert capsys.readouterr().out == (
            "complete response\n"
            f"响应结果已保存到：{expected_path}\n"
        )
        assert expected_path.read_text(encoding="utf-8") == (
            "[reasoning]\ncareful analysis\n[content]\ncomplete response\n"
        )
        assert run_code_agent.await_args.kwargs["output_stream"] is None
