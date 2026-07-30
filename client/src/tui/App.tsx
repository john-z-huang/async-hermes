import { Box, Text, useApp, useInput } from "ink";
import TextInput from "ink-text-input";
import { useCallback, useEffect, useRef, useState } from "react";

import type { AgentEvent } from "../generated/v1/agent.js";
import type { HermesRpcClient, TurnSubscription } from "../rpc/hermes-client.js";
import { RpcConnectionError } from "../rpc/hermes-client.js";
import { applyAgentEvent, initialTuiState, selectSession, type DisplayEvent } from "./state.js";

interface ActiveTurn {
  sessionId: string;
  turnId?: string;
  subscription: TurnSubscription;
}

export interface AppProps {
  client: HermesRpcClient;
}

function eventColor(kind: DisplayEvent["kind"]): string {
  return {
    content: "white",
    reasoning: "gray",
    "tool-started": "yellow",
    "tool-finished": "green",
    artifact: "magenta",
    completed: "green",
    failed: "red",
    cancelled: "yellow",
  }[kind];
}

function EventLine({ event }: { event: DisplayEvent }) {
  return (
    <Text color={eventColor(event.kind)}>
      [{event.kind}] {event.text}
    </Text>
  );
}

/** React/Ink 终端视图：只管理展示状态，业务历史始终留在 Python 服务。 */
export function App({ client }: AppProps) {
  const { exit } = useApp();
  const [state, setState] = useState(initialTuiState);
  const [input, setInput] = useState("");
  const [activeTurnId, setActiveTurnId] = useState<string>();
  const [scrollOffset, setScrollOffset] = useState(0);
  const activeTurn = useRef<ActiveTurn | undefined>(undefined);
  const selectedSession = useRef<string | undefined>(undefined);

  const createSession = useCallback(async () => {
    try {
      const session = await client.createSession();
      selectedSession.current = session.sessionId;
      setState((previous) => selectSession({ ...previous, connectionError: undefined }, session.sessionId));
    } catch (error) {
      setState((previous) => ({ ...previous, connectionError: `连接错误：${String(error)}` }));
    }
  }, [client]);

  useEffect(() => {
    void createSession();
    return () => {
      activeTurn.current?.subscription.cancel();
      client.close();
    };
  }, [client, createSession]);

  const consumeEvent = (event: AgentEvent) => {
    if (activeTurn.current) activeTurn.current.turnId = event.turnId;
    setState((previous) => applyAgentEvent(previous, event));
    if (event.turnCompleted || event.turnFailed || event.turnCancelled) setActiveTurnId(undefined);
  };

  const submit = async (value: string) => {
    const question = value.trim();
    const sessionId = selectedSession.current;
    if (!question || !sessionId || activeTurn.current) return;
    setInput("");
    const subscription = client.runTurn(sessionId, question);
    activeTurn.current = { sessionId, subscription };
    setActiveTurnId("pending");
    try {
      for await (const event of subscription.events) consumeEvent(event);
    } catch (error) {
      const label = error instanceof RpcConnectionError ? "连接错误" : "RPC 错误";
      setState((previous) => ({ ...previous, connectionError: `${label}：${String(error)}` }));
    } finally {
      activeTurn.current = undefined;
      setActiveTurnId(undefined);
    }
  };

  const cancel = async () => {
    const active = activeTurn.current;
    if (!active) return;
    active.subscription.cancel();
    if (active.turnId) {
      try {
        await client.cancelTurn(active.sessionId, active.turnId);
      } catch (error) {
        setState((previous) => ({ ...previous, connectionError: `RPC 错误：${String(error)}` }));
      }
    }
  };

  useInput((character, key) => {
    if (key.ctrl && character === "c") exit();
    if (key.ctrl && character === "x") void cancel();
    if (character === "n" && !input) void createSession();
    if (key.tab && state.sessionIds.length > 1) {
      const index = state.sessionIds.indexOf(state.selectedSessionId ?? "");
      const nextSessionId = state.sessionIds[(index + 1) % state.sessionIds.length];
      selectedSession.current = nextSessionId;
      setState((previous) => ({ ...previous, selectedSessionId: nextSessionId }));
    }
    if (key.upArrow) setScrollOffset((value) => Math.max(0, value - 1));
    if (key.downArrow) setScrollOffset((value) => value + 1);
  });

  const selectedEvents = Object.values(state.turns)
    .flatMap((turn) => turn.events)
    .slice(scrollOffset);

  return (
    <Box flexDirection="column">
      <Text bold color="cyan">
        Hermes TUI
      </Text>
      <Text>
        会话：{state.selectedSessionId ?? "正在创建…"} {activeTurnId ? "运行中" : "空闲"}
      </Text>
      <Text dimColor>n 新建会话 · Tab 切换 · Ctrl+X 取消 · ↑/↓ 滚动 · Ctrl+C 退出</Text>
      {state.connectionError && <Text color="red">{state.connectionError}</Text>}
      {state.protocolError && <Text color="red">协议错误：{state.protocolError}</Text>}
      <Box flexDirection="column" marginTop={1} minHeight={4}>
        {selectedEvents.length === 0 ? (
          <Text dimColor>等待输入…</Text>
        ) : (
          selectedEvents.map((event) => <EventLine key={`${event.sequence}-${event.kind}`} event={event} />)
        )}
      </Box>
      <Box marginTop={1}>
        <Text color="cyan">&gt; </Text>
        <TextInput
          value={input}
          onChange={setInput}
          onSubmit={(value) => void submit(value)}
          placeholder="输入问题后按 Enter"
        />
      </Box>
    </Box>
  );
}
