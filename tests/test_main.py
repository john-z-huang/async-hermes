"""Hermes 分层架构的单元与兼容性测试。"""

from __future__ import annotations

import asyncio
from contextlib import redirect_stderr
import hashlib
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agents import FunctionTool
from agents.tool_context import ToolContext
from openai.types.responses import (
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseTextDeltaEvent,
)
import pytest

import main
from hermes.application import (
    AgentService,
    RunnerEvent,
    RunnerEventType,
    TurnRequest,
)
from hermes.domain import AgentEvent, AgentEventType, TurnStatus
from hermes.infrastructure.agents_sdk_runner import (
    AgentsSdkRunner,
    AgentsSdkRunnerConfig,
)
from hermes.infrastructure.persistence import persist_agent_output
from hermes.infrastructure.workspace_tools import (
    DEFAULT_AGENT_PERMISSIONS,
    build_read_only_workspace_tool,
    execute_read_only_shell,
    validate_read_only_command,
)
from hermes.interfaces import cli


def make_stream_result(
    *events: object,
    final_output: str = "done",
    input_list: list[object] | None = None,
) -> SimpleNamespace:
    async def stream_events():
        for event in events:
            yield event

    return SimpleNamespace(
        final_output=final_output,
        stream_events=stream_events,
        to_input_list=Mock(return_value=input_list or []),
    )


def raw_event(data: object) -> SimpleNamespace:
    return SimpleNamespace(type="raw_response_event", data=data)


async def collect_events(events: object) -> list[object]:
    return [event async for event in events]


class FakeRunner:
    def __init__(
        self,
        events: list[RunnerEvent] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events or []
        self.error = error
        self.requests: list[TurnRequest] = []

    async def run(self, request: TurnRequest):
        self.requests.append(request)
        if self.error:
            raise self.error
        for event in self.events:
            yield event


class ValidateReadOnlyCommandTests(unittest.TestCase):
    def test_allows_read_only_commands(self) -> None:
        for command in (
            ("pwd",),
            ("rg", "--files"),
            ("git", "status", "--short"),
            ("find", ".", "-maxdepth", "1"),
        ):
            with self.subTest(command=command):
                self.assertIsNone(validate_read_only_command(command))

    def test_rejects_unknown_mutating_and_external_paths(self) -> None:
        for command in (
            (),
            ("rm", "file.txt"),
            ("git", "commit", "-m", "message"),
            ("find", ".", "-delete"),
            ("cat", "/etc/passwd"),
            ("cat", "../secret.txt"),
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(validate_read_only_command(command))

    def test_rejects_symlink_escaping_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / "outside").symlink_to(Path(__file__).resolve())
            self.assertIsNotNone(
                validate_read_only_command(("cat", "outside"), workspace=workspace)
            )


class ReadOnlyShellTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_allowed_commands_in_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            (workspace / "example.txt").touch()
            output = await execute_read_only_shell(
                ["pwd && ls -1p"], workspace=workspace
            )
        self.assertIn(str(workspace.resolve()), output)
        self.assertIn("example.txt", output)

    async def test_rejects_command_without_process(self) -> None:
        with patch(
            "hermes.infrastructure.workspace_tools.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as execute:
            output = await execute_read_only_shell(["rm important-file"])
        self.assertIn("不在只读命令白名单", output)
        execute.assert_not_awaited()

    async def test_function_tool_uses_shared_executor(self) -> None:
        tool = build_read_only_workspace_tool()
        self.assertIsInstance(tool, FunctionTool)
        self.assertEqual(tool.name, "inspect_workspace")
        self.assertTrue(tool.strict_json_schema)
        arguments = '{"commands":["pwd"]}'
        context = ToolContext(
            context=None,
            tool_name=tool.name,
            tool_call_id="call-1",
            tool_arguments=arguments,
        )
        with patch(
            "hermes.infrastructure.workspace_tools.execute_read_only_shell",
            new_callable=AsyncMock,
            return_value="workspace",
        ) as execute:
            output = await tool.on_invoke_tool(context, arguments)
        self.assertEqual(output, "workspace")
        execute.assert_awaited_once_with(
            ["pwd"],
            workspace=Path.cwd().resolve(),
            permissions=DEFAULT_AGENT_PERMISSIONS,
        )


class AgentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_emits_ordered_events_and_commits_successful_history(self) -> None:
        history = (
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
        )
        runner = FakeRunner(
            [
                RunnerEvent(RunnerEventType.CONTENT_DELTA, "ans"),
                RunnerEvent(
                    RunnerEventType.COMPLETED,
                    "answer",
                    history=history,
                ),
            ]
        )
        service = AgentService(runner)
        session = service.create_session("session-1")

        events = await collect_events(service.run_turn(session.id, "first"))

        self.assertEqual([event.sequence for event in events], [1, 2, 3])
        self.assertEqual(
            [event.type for event in events],
            [
                AgentEventType.TURN_STARTED,
                AgentEventType.CONTENT_DELTA,
                AgentEventType.TURN_COMPLETED,
            ],
        )
        self.assertEqual(session.history, list(history))
        self.assertEqual(session.turns[0].status, TurnStatus.COMPLETED)

    async def test_next_turn_receives_structured_history(self) -> None:
        first_history = (
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
        )
        runner = FakeRunner(
            [RunnerEvent(RunnerEventType.COMPLETED, "answer", history=first_history)]
        )
        service = AgentService(runner)
        session = service.create_session("session-1")
        await collect_events(service.run_turn(session.id, "first"))
        runner.events = [
            RunnerEvent(
                RunnerEventType.COMPLETED,
                "second answer",
                history=(*first_history, {"role": "user", "content": "second"}),
            )
        ]

        await collect_events(service.run_turn(session.id, "second"))

        self.assertTrue(runner.requests[0].first_turn)
        self.assertFalse(runner.requests[1].first_turn)
        self.assertEqual(runner.requests[1].history, first_history)

    async def test_failure_does_not_pollute_previous_history(self) -> None:
        runner = FakeRunner(error=RuntimeError("provider unavailable"))
        service = AgentService(runner)
        session = service.create_session("session-1")
        session.history[:] = [{"role": "assistant", "content": "completed"}]

        events = await collect_events(service.run_turn(session.id, "next"))

        self.assertEqual(events[-1].type, AgentEventType.TURN_FAILED)
        self.assertEqual(session.history, [{"role": "assistant", "content": "completed"}])
        self.assertEqual(session.turns[-1].status, TurnStatus.FAILED)

    async def test_missing_completed_event_is_failure(self) -> None:
        service = AgentService(
            FakeRunner([RunnerEvent(RunnerEventType.CONTENT_DELTA, "partial")])
        )
        session = service.create_session("session-1")

        events = await collect_events(service.run_turn(session.id, "question"))

        self.assertEqual(events[-1].type, AgentEventType.TURN_FAILED)
        self.assertEqual(session.history, [])

    async def test_cancelled_turn_preserves_history(self) -> None:
        runner = FakeRunner(error=asyncio.CancelledError())
        service = AgentService(runner)
        session = service.create_session("session-1")
        session.history[:] = [{"role": "assistant", "content": "completed"}]

        events = await collect_events(service.run_turn(session.id, "next"))

        self.assertEqual(events[-1].type, AgentEventType.TURN_CANCELLED)
        self.assertEqual(session.history, [{"role": "assistant", "content": "completed"}])
        self.assertEqual(session.turns[-1].status, TurnStatus.CANCELLED)


class AgentsSdkRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_converts_sdk_deltas_and_completion(self) -> None:
        result = make_stream_result(
            raw_event(ResponseTextDeltaEvent.model_construct(delta="answer")),
            raw_event(
                ResponseReasoningSummaryTextDeltaEvent.model_construct(
                    delta="thinking"
                )
            ),
            SimpleNamespace(
                type="run_item_stream_event",
                item=SimpleNamespace(
                    type="tool_call_item",
                    title="inspect_workspace",
                    description=None,
                ),
            ),
            SimpleNamespace(
                type="run_item_stream_event",
                item=SimpleNamespace(
                    type="tool_call_output_item",
                    output="main.py",
                ),
            ),
            final_output="answer",
            input_list=[{"role": "assistant", "content": "answer"}],
        )
        runner = AgentsSdkRunner(
            AgentsSdkRunnerConfig(enable_reasoning=True)
        )
        with patch(
            "hermes.infrastructure.agents_sdk_runner.Runner.run_streamed",
            return_value=result,
        ):
            events = await collect_events(
                runner.run(TurnRequest("question", (), True))
            )

        self.assertEqual(
            [event.type for event in events],
            [
                RunnerEventType.CONTENT_DELTA,
                RunnerEventType.REASONING_DELTA,
                RunnerEventType.TOOL_STARTED,
                RunnerEventType.TOOL_FINISHED,
                RunnerEventType.COMPLETED,
            ],
        )
        self.assertEqual(events[-1].history, ({"role": "assistant", "content": "answer"},))

    async def test_appends_new_question_to_existing_history(self) -> None:
        captured: dict[str, object] = {}
        result = make_stream_result()

        def run_streamed(agent: object, task_input: object) -> SimpleNamespace:
            captured["input"] = task_input
            return result

        runner = AgentsSdkRunner()
        history = ({"role": "assistant", "content": "previous"},)
        with patch(
            "hermes.infrastructure.agents_sdk_runner.Runner.run_streamed",
            side_effect=run_streamed,
        ):
            await collect_events(
                runner.run(TurnRequest("next", history, False))
            )
        self.assertEqual(
            captured["input"],
            [*history, {"role": "user", "content": "next"}],
        )

    async def test_configures_reasoning_and_workspace_tool(self) -> None:
        captured: dict[str, object] = {}

        def run_streamed(agent: object, task_input: object) -> SimpleNamespace:
            captured["agent"] = agent
            return make_stream_result()

        runner = AgentsSdkRunner(
            AgentsSdkRunnerConfig(enable_reasoning=True, reason_effect="high")
        )
        with patch(
            "hermes.infrastructure.agents_sdk_runner.Runner.run_streamed",
            side_effect=run_streamed,
        ):
            await collect_events(runner.run(TurnRequest("question", (), True)))
        agent = captured["agent"]
        self.assertEqual(agent.model_settings.reasoning.effort, "high")
        self.assertEqual([tool.name for tool in agent.tools], ["inspect_workspace"])


class EventRendererTests(unittest.TestCase):
    def test_labels_reasoning_and_content_outside_core(self) -> None:
        stream = StringIO()
        renderer = cli.EventRenderer(stream, None, enable_reasoning=True)
        base = {"session_id": "s", "turn_id": "t"}
        renderer.render(
            AgentEvent(**base, sequence=1, type=AgentEventType.REASONING_DELTA, text="why")
        )
        renderer.render(
            AgentEvent(**base, sequence=2, type=AgentEventType.CONTENT_DELTA, text="answer")
        )
        renderer.finish()
        self.assertEqual(
            stream.getvalue(), "[reasoning]\nwhy\n[content]\nanswer\n"
        )

    def test_core_events_do_not_contain_terminal_labels(self) -> None:
        event = AgentEvent(
            "s", "t", 1, AgentEventType.CONTENT_DELTA, text="answer"
        )
        self.assertEqual(event.text, "answer")
        self.assertNotIn("[content]", event.text)


class ParseArgsTests(unittest.TestCase):
    def test_defaults_and_one_shot_validation(self) -> None:
        with patch(
            "hermes.interfaces.cli.Path.cwd", return_value=Path("/tmp")
        ), patch(
            # 隔离本机 ~/.async-hermes/config.toml，验证无配置时默认 workspace 为 cwd。
            "hermes.interfaces.cli.default_config_path", return_value=None
        ):
            args = cli.parse_args([])
        self.assertEqual(args.running_mode, "loop")
        self.assertIsNone(args.question)
        self.assertTrue(args.enable_stream_output)
        self.assertEqual(args.workspace, Path("/tmp").resolve())
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            cli.parse_args(["--running-mode", "one-shot"])

    def test_accepts_existing_cli_options(self) -> None:
        args = cli.parse_args(
            [
                "--running-mode", "one-shot",
                "--question", "test",
                "--enable-stream-output", "false",
                "--enable-reasoning", "true",
                "--reason-effect", "high",
                "--output-file", "results/answer.txt",
            ]
        )
        self.assertFalse(args.enable_stream_output)
        self.assertTrue(args.enable_reasoning)
        self.assertEqual(args.reason_effect, "high")


class PersistenceTests:
    def test_default_and_custom_paths(self, tmp_path: Path) -> None:
        output = "完整响应"
        expected_hash = hashlib.sha256(output.encode()).hexdigest()[:16]
        default_path = persist_agent_output(output, workspace=tmp_path)
        assert default_path == tmp_path / ".agents" / f"response-{expected_hash}.txt"
        custom_path = persist_agent_output(
            output, workspace=tmp_path, output_file="results/answer.txt"
        )
        assert custom_path.read_text(encoding="utf-8") == output

    @pytest.mark.parametrize("output_file", ["/tmp/a.txt", "../a.txt", "."])
    def test_rejects_escaping_paths(
        self, tmp_path: Path, output_file: str
    ) -> None:
        with pytest.raises(ValueError, match="workspace"):
            persist_agent_output(
                "response", workspace=tmp_path, output_file=output_file
            )


class CliTests:
    def test_one_shot_and_loop_reuse_application_service(
        self, tmp_path: Path
    ) -> None:
        service = Mock()
        service.create_session.return_value = SimpleNamespace(id="session-1")
        with (
            patch("hermes.interfaces.cli.build_service", return_value=service),
            patch("hermes.interfaces.cli._run_and_persist") as run,
        ):
            cli.main(
                [
                    "--running-mode", "one-shot",
                    "--question", "first",
                    "--workspace", str(tmp_path),
                ]
            )
        self.assertIs(run.call_args.kwargs["service"], service)
        self.assertEqual(run.call_args.kwargs["session_id"], "session-1")

    def test_loop_ignores_empty_input_and_continues_after_failure(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        service = Mock()
        service.create_session.return_value = SimpleNamespace(id="session-1")
        with (
            patch("hermes.interfaces.cli.build_service", return_value=service),
            patch(
                "hermes.interfaces.cli._run_and_persist",
                side_effect=[RuntimeError("failed"), None],
            ) as run,
            patch("builtins.input", side_effect=["", "first", "second", "/exit"]),
        ):
            cli.main(["--workspace", str(tmp_path)])
        self.assertEqual(run.call_count, 2)
        self.assertIn("本轮请求失败：failed", capsys.readouterr().err)

    def test_main_py_remains_compatible_entrypoint(self) -> None:
        self.assertIs(main.main, cli.main)

    def test_pyproject_exposes_py_hermes_console_script(self) -> None:
        import tomllib

        project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            project["project"]["scripts"]["py-hermes"],
            "hermes.interfaces.cli:main",
        )
