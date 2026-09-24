"""
The distiller (M7). No network: the model is a scripted Messages API behind a mock
transport, and Memento is a stub MCP session. The real server and a real model
meet in evals/distilling.py.

    cd distiller && uv run python -m unittest -v
"""

import ast
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import anthropic
import httpx2
from mcp.types import CallToolResult, TextContent, Tool

import distil

ALL_TOOLS = ["remember", "save_digest", "recall", "timeline", "list_tags",
             "complete_reminder", "inbox", "close_capture", "forget"]  # fmt: skip
NOTE = {
    "id": "note-1",
    "source": "pocket",
    "captured_at": "2026-09-24T08:10:00+01:00",
    "received_at": "2026-09-24T08:11:00+01:00",
    "title": "",
    "transcript": "Slept badly. Knee sore after padel.",
    "revisions": [],
    "hints": {},
    "entries": [],
}


def tool(name: str) -> Tool:
    return Tool(name=name, description=f"{name} tool", input_schema={"type": "object"})


class StubMemento:
    """An MCP session that records calls and answers like Memento."""

    def __init__(self, notes=(NOTE,)):
        self.calls: list[tuple[str, dict]] = []
        self.notes = list(notes)

    async def list_tools(self):
        return type("Listed", (), {"tools": [tool(n) for n in ALL_TOOLS]})()

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments or {}))
        if name == "inbox":
            body = {"now": "2026-09-24T08:15:00+01:00", "waiting": len(self.notes),
                    "captures": self.notes, "current_entries": []}  # fmt: skip
        else:
            body = {"now": "2026-09-24T08:15:00+01:00", "ok": True}
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(body))], structured_content=body
        )


def message(content: list[dict], stop: str) -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": distil.MODEL,
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0},
    }  # fmt: skip


def use(i: int, name: str, args: dict) -> dict:
    return message([{"type": "tool_use", "id": f"tu_{i}", "name": name, "input": args}], "tool_use")


class ScriptedModel:
    """Answers each Messages API request with the next scripted reply, and keeps the requests."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def client(self) -> anthropic.AsyncAnthropic:
        def handle(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(json.loads(request.content))
            reply = self.replies.pop(0)
            return httpx2.Response(reply if isinstance(reply, int) else 200,
                                   json={} if isinstance(reply, int) else reply)  # fmt: skip

        return anthropic.AsyncAnthropic(
            api_key="test", max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handle)),
        )  # fmt: skip


SLEPT = {"raw_text": "Slept badly.", "claim": "Slept badly on 23 Sep 2026.", "kind": "memory"}


class DistillerTests(unittest.IsolatedAsyncioTestCase):
    def test_the_distiller_is_a_client(self):
        """0013: it never imports server code."""
        tree = ast.parse(Path(distil.__file__).read_text())
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                    for a in n.names}  # fmt: skip
        imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom) and n.module}  # fmt: skip
        self.assertFalse(imported & {"memories", "config", "django"})

    def test_it_is_never_offered_forget_digest_or_inbox(self):
        """0019: the model's reach is narrow, and in a fixed order so the prefix caches."""
        names = [t.name for t in distil.tools_for([tool(n) for n in ALL_TOOLS], None, "note-1")]
        self.assertEqual(names, sorted(distil.TOOLS))
        self.assertFalse({"forget", "save_digest", "inbox"} & set(names))

    def test_instructions_are_the_skill_verbatim(self):
        text = distil.instructions()
        self.assertIn((distil.SKILL / "SKILL.md").read_text(), text)
        self.assertIn((distil.SKILL / "references" / "distilling.md").read_text(), text)
        self.assertEqual(text, distil.instructions())  # stable, so it caches

    async def test_a_note_becomes_entries_and_is_closed(self):
        memento = StubMemento()
        model = ScriptedModel([
            use(1, "remember", SLEPT),
            use(2, "close_capture", {"capture_id": "note-1", "status": "processed"}),
            message([{"type": "text", "text": "processed: 1 entry"}], "end_turn"),
        ])  # fmt: skip
        listed = (await memento.list_tools()).tools
        out = await distil.distil_note(model.client(), memento, listed, NOTE, [], "now")
        self.assertEqual(out["said"], "processed: 1 entry")
        self.assertEqual([c[0] for c in memento.calls], ["remember", "close_capture"])
        first = model.requests[0]
        self.assertEqual(first["model"], "claude-sonnet-5")
        self.assertIn("You are Memento's distiller", json.dumps(first["system"]))
        self.assertIn("Slept badly. Knee sore after padel.", first["messages"][0]["content"])

    async def test_every_entry_points_back_to_its_note(self):
        """No capture, or the wrong one, is replaced: nothing escapes into a new inbox note."""
        memento = StubMemento()
        model = ScriptedModel([
            use(1, "remember", {**SLEPT, "capture": "some-other-note"}),
            use(2, "remember", {"raw_text": "Knee sore after padel."}),
            message([{"type": "text", "text": "processed"}], "end_turn"),
        ])  # fmt: skip
        listed = (await memento.list_tools()).tools
        await distil.distil_note(model.client(), memento, listed, NOTE, [], "now")
        self.assertEqual([args["capture"] for _, args in memento.calls], ["note-1", "note-1"])

    async def test_each_run_looks_at_a_35_minute_window(self):
        memento = StubMemento(notes=())
        with patch.object(distil, "memento", fake_connection(memento)):
            code = await distil.run("url", "token", since=None, dry_run=False)
        self.assertEqual(code, 0)
        (name, args), *_ = memento.calls
        since = datetime.fromisoformat(args["received_since"])
        self.assertEqual(name, "inbox")
        self.assertAlmostEqual(
            (datetime.now(UTC) - since).total_seconds(), timedelta(minutes=35).total_seconds(),
            delta=5,
        )  # fmt: skip

    async def test_a_failed_model_call_fails_the_run_and_leaves_the_note(self):
        """Render marks the run failed; the next run's window still covers the note."""
        memento = StubMemento()
        llm = ScriptedModel([529]).client()
        with (
            patch.object(distil, "memento", fake_connection(memento)),
            patch.object(distil.anthropic, "AsyncAnthropic", lambda: llm),
        ):
            code = await distil.run("url", "token", since=None, dry_run=False)
        self.assertEqual(code, 1)
        self.assertNotIn("close_capture", [c[0] for c in memento.calls])


def fake_connection(session):
    class Connection:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return Connection


if __name__ == "__main__":
    unittest.main()
