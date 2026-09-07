"""
The agent loop over the MCP server.

Built on the Claude Agent SDK, which already runs the actual "loop of tool
calls until the model produces text" for us against the Claude CLI -- what
this module adds is: wiring the domain MCP server in as the agent's only
tool surface (all built-in file/bash tools are disabled -- the agent can act
on the account only through server/main.py's 13 tools), the system prompt
in agent/system_prompt.py, and a thin turn-based API the UI drives.

Run standalone as a terminal chat with:  python -m agent.main
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agent.system_prompt import SYSTEM_PROMPT

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = os.environ.get("DOTTHOUSE_MODEL", "claude-sonnet-5")
MCP_SERVER_NAME = "dotthouse"


@dataclass
class ToolCallRecord:
    name: str
    input: dict
    result: Optional[str] = None
    is_error: bool = False


@dataclass
class TurnResult:
    reply: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    num_turns: int = 0
    cost_usd: float = 0.0
    is_error: bool = False


def _build_options(model: str = DEFAULT_MODEL) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=model,
        cwd=str(REPO_ROOT),
        tools=[],  # disable ALL built-in tools (Bash, Read, Write, ...) --
        # the agent's only capability is the domain MCP server below.
        mcp_servers={
            MCP_SERVER_NAME: {
                "type": "stdio",
                # sys.executable, not the bare string "python": this
                # subprocess MUST be the same interpreter this process is
                # running under (the uv-managed venv with mcp/pydantic
                # installed). A bare "python" resolves through PATH at
                # spawn time, which on some systems -- notably Windows,
                # where "python" isn't guaranteed to exist or to be the
                # venv's interpreter -- can silently launch a different,
                # dependency-less Python. That subprocess then fails to
                # import server.main, the MCP connection never comes up,
                # and the agent is left with zero real tools despite the
                # system prompt instructing it to call named ones -- which
                # is exactly the failure mode where the model starts
                # narrating fake tool-call-shaped text instead of actually
                # invoking anything, since no real tool is there to call.
                "command": sys.executable,
                "args": ["-m", "server.main"],
                "cwd": str(REPO_ROOT),
                "env": {**os.environ},
            },
        },
        permission_mode="bypassPermissions",  # only tool available is our own
        # 13-tool domain surface (built-ins are off), so there is nothing to
        # gate behind an interactive approval prompt during a live call.
        max_turns=30,
    )


class OnboardingAgent:
    """One long-lived agent session for one onboarding call. Wraps
    ClaudeSDKClient: connect() once, then turn() per operator message,
    reusing the same underlying MCP server subprocess (and therefore the
    same in-memory account state) for the whole call."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._client = ClaudeSDKClient(options=_build_options(model))
        self._connected = False

    async def connect(self) -> None:
        if not self._connected:
            await self._client.connect()
            self._connected = True

    async def disconnect(self) -> None:
        if self._connected:
            await self._client.disconnect()
            self._connected = False

    async def turn(self, message: str) -> TurnResult:
        await self.connect()
        await self._client.query(message)

        reply_parts: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        pending: dict[str, ToolCallRecord] = {}
        num_turns = 0
        cost_usd = 0.0
        is_error = False

        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        reply_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        rec = ToolCallRecord(name=_short_tool_name(block.name), input=block.input)
                        tool_calls.append(rec)
                        pending[block.id] = rec
            elif isinstance(msg, UserMessage):
                # UserMessage carries tool results back from the MCP server
                content = getattr(msg, "content", None)
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, ToolResultBlock):
                            rec = pending.get(block.tool_use_id)
                            if rec is not None:
                                rec.result = _stringify(block.content)
                                rec.is_error = bool(block.is_error)
            elif isinstance(msg, ResultMessage):
                num_turns = msg.num_turns
                cost_usd = msg.total_cost_usd or 0.0
                is_error = bool(msg.is_error)

        return TurnResult(
            reply="\n".join(p for p in reply_parts if p.strip()) or "(nessuna risposta testuale)",
            tool_calls=tool_calls,
            num_turns=num_turns,
            cost_usd=cost_usd,
            is_error=is_error,
        )


def _short_tool_name(name: str) -> str:
    prefix = f"mcp__{MCP_SERVER_NAME}__"
    return name[len(prefix):] if name.startswith(prefix) else name


def _stringify(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            text = getattr(c, "text", None)
            parts.append(text if text is not None else str(c))
        return "\n".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# terminal chat, for quick manual testing / a no-UI fallback
# ---------------------------------------------------------------------------

async def _repl() -> None:
    agent = OnboardingAgent()
    print(f"Dott.House onboarding copilot (model={DEFAULT_MODEL}). Ctrl-D to quit.")
    print("Suggestion: start with something like 'ho l'export davanti, iniziamo'.\n")
    try:
        while True:
            try:
                message = input("operatore> ").strip()
            except EOFError:
                print()
                break
            if not message:
                continue
            result = await agent.turn(message)
            for tc in result.tool_calls:
                flag = "ERR" if tc.is_error else "ok"
                print(f"  [tool:{flag}] {tc.name}({tc.input})")
            print(f"agente> {result.reply}\n")
    finally:
        await agent.disconnect()


if __name__ == "__main__":
    asyncio.run(_repl())
