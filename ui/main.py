"""
Minimal local web UI: import on one side, live configuration state in a
table, chat with the agent on the other. Typing only (see PRIORITIES.md for
why no voice input).

No styling effort has gone into this on purpose -- the brief weights
appearance at zero and asks instead for a flow that can be driven end to
end without reading the code. Run with: python -m ui.main
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from agent.main import DEFAULT_MODEL, OnboardingAgent

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "out"
LIVE_STATE_PATH = OUT_DIR / "_live_state.json"
CONFIG_PATH = OUT_DIR / "configuration.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = Flask(__name__, static_folder=None)

# In-memory chat history for this call. One operator, one process -- no
# session/user handling needed (see PRIORITIES.md, "no auth / multi-tenant").
history: list[dict] = []

# _live_state.json is a snapshot of THIS PROCESS's in-memory agent/MCP
# session (see server/main.py's _live_state_snapshot -- it's written fresh
# after every mutation, purely so /api/state has something to read). A new
# process always starts with a brand-new, empty in-memory Store, but a file
# from the *previous* process's last run survives on disk -- so without this,
# restarting the server (new terminal, closed the old one, etc.) makes the
# state pane show the last run's "40 configured" as if it were live, while
# the actual agent session underneath has nothing imported yet. Every tool
# call then fails with "no account imported", which is confusing to watch
# play out in the chat. out/configuration.json is deliberately left alone
# here -- that's the real deliverable and is meant to survive restarts (see
# README): only the working snapshot is invalidated.
if LIVE_STATE_PATH.exists():
    LIVE_STATE_PATH.unlink()


# ---------------------------------------------------------------------------
# background event loop running the (async) agent, driven from sync Flask
# request handlers via run_coroutine_threadsafe
# ---------------------------------------------------------------------------

class AgentRunner:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.agent: OnboardingAgent | None = None
        self.model = DEFAULT_MODEL
        self._lock = threading.Lock()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _submit(self, coro, timeout: float = 300.0):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)

    def new_session(self, model: str | None = None) -> None:
        with self._lock:
            if self.agent is not None:
                try:
                    self._submit(self.agent.disconnect(), timeout=30)
                except Exception:
                    pass
            self.model = model or self.model
            self.agent = OnboardingAgent(model=self.model)

    def turn(self, message: str):
        with self._lock:
            if self.agent is None:
                self.agent = OnboardingAgent(model=self.model)
            agent = self.agent
        return self._submit(agent.turn(message))


runner = AgentRunner()


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/state")
def api_state():
    if LIVE_STATE_PATH.exists():
        data = json.loads(LIVE_STATE_PATH.read_text(encoding="utf-8"))
    else:
        data = {"summary": None, "listings": []}
    data["imported"] = LIVE_STATE_PATH.exists()
    data["exported_to_disk"] = CONFIG_PATH.exists()
    return jsonify(data)


@app.get("/api/history")
def api_history():
    return jsonify(history)


@app.post("/api/reset")
def api_reset():
    # Only the working snapshot is cleared here -- CONFIG_PATH (the actual
    # deliverable, out/configuration.json) is deliberately left alone. This
    # used to delete both, which silently contradicted the "New call
    # (reset)" button's own confirm dialog and this file's docstring/the
    # README, both of which promise the last saved copy survives a reset.
    body = request.get_json(silent=True) or {}
    runner.new_session(model=body.get("model"))
    history.clear()
    if LIVE_STATE_PATH.exists():
        LIVE_STATE_PATH.unlink()
    return jsonify({"ok": True, "model": runner.model})


@app.post("/api/import")
def api_import():
    return _turn("Ho l'export davanti, apriamo l'importazione e iniziamo la chiamata.")


@app.post("/api/chat")
def api_chat():
    body = request.get_json(force=True)
    message = (body or {}).get("message", "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400
    return _turn(message)


def _turn(message: str):
    history.append({"role": "operator", "text": message})
    try:
        result = runner.turn(message)
    except Exception as e:  # surfaced to the UI rather than a bare 500
        err = f"Errore lato agente: {e}"
        history.append({"role": "agent", "text": err, "tool_calls": [], "is_error": True})
        return jsonify({"error": err}), 500
    entry = {
        "role": "agent",
        "text": result.reply,
        "tool_calls": [
            {"name": tc.name, "input": tc.input, "result": tc.result, "is_error": tc.is_error}
            for tc in result.tool_calls
        ],
        "num_turns": result.num_turns,
        "cost_usd": result.cost_usd,
        "is_error": result.is_error,
    }
    history.append(entry)
    return jsonify(entry)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
