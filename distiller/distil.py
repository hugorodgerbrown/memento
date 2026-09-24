"""
Memento's distiller (M7; ADR 0013 and 0019).

A scheduled MCP client. Each run reads the inbox notes that arrived in the last
35 minutes and, one note at a time, has one chosen model turn it into entries,
with the Memento skill as its instructions. What it would ask the user about, it
leaves in the inbox for them.

It reaches Memento only through MCP, with its own token, and never imports
`memories` or `config`. The model provider's key lives here, never on the server.

    MEMENTO_URL=http://localhost:8000/mcp MEMENTO_TOKEN=... ANTHROPIC_API_KEY=... \\
        uv run python distil.py [--since 2026-09-24T08:00:00+01:00] [--dry-run]
"""

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anthropic
import httpx2
from anthropic.lib.tools import ToolError, beta_async_tool
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

SKILL = Path(__file__).resolve().parents[1] / "skill" / "memento"
WINDOW = timedelta(minutes=35)  # runs every 15: two or three chances per note (0019)
MODEL = os.environ.get("DISTILLER_MODEL", "claude-sonnet-5")
MAX_ITERATIONS = 25  # model turns per note; a note that needs more is left for the user
# The model is offered these and nothing else: never forget, never save_digest, and
# never inbox, because the code decides which note it works on (0019).
TOOLS = ("complete_reminder", "close_capture", "list_tags", "recall", "remember", "timeline")

PREAMBLE = """\
You are Memento's distiller. You run on a schedule with nobody watching, and you \
work on one inbox note at a time: the note in the user's message, which holds the \
user's own words. Follow the Memento skill below, as a client distilling the inbox.

Because nobody is there to answer:
- Where the skill says to ask the user ("Log this?", someone else's private news, \
a fact inside something being drafted for someone else), save nothing from the \
note and do not close it. It waits in the inbox for the user.
- If nothing in the note is worth keeping (a question, a hypothetical, fiction, a \
repeat of what is already saved), call close_capture with status "dismissed".
- Otherwise save each entry with remember, with raw_text an exact excerpt of the \
transcript, then call close_capture with status "processed".
- If the server keeps refusing an entry, leave the note open rather than saving \
something wrong. Never send raw_text alone: the note is already in the inbox.

Pass model_name "{model}" on every remember. Your final message goes to the \
operator's log, not the user: one line saying processed, dismissed or left, and why.

---

"""


def instructions(model: str = MODEL) -> str:
    """The skill, verbatim, after a short preamble. Stable per deployment, so it caches."""
    parts = [
        SKILL / "SKILL.md",
        SKILL / "references" / "distilling.md",
        SKILL / "references" / "capture.md",
    ]
    return PREAMBLE.format(model=model) + "\n\n".join(p.read_text() for p in parts)


def note_message(capture: dict, current: list[dict], now: str) -> str:
    """What the model sees about the note: the note, what might be updated, and the time."""
    return (
        f"Now: {now}\n\n"
        f"Inbox note to distil:\n{json.dumps(capture, indent=2, ensure_ascii=False)}\n\n"
        "The user's most recent current entries, which this note might update:\n"
        f"{json.dumps(current, indent=2, ensure_ascii=False)}"
    )


def tools_for(listed: list, session, capture_id: str) -> list:
    """
    Memento's tools as runnable tools, narrowed to TOOLS, in a fixed order so the
    request prefix caches. `remember` always carries this note's id, so every entry
    points back to the note and nothing can escape into a new inbox capture.
    """
    by_name = {t.name: t for t in listed if t.name in TOOLS}
    tools = []
    for name in sorted(by_name):
        tool = by_name[name]
        if name != "remember":
            tools.append(async_mcp_tool(tool, session))
            continue
        tools.append(pinned_remember(tool, session, capture_id))
    return tools


def pinned_remember(tool, session, capture_id: str):
    async def remember(**kwargs: Any):
        result = await session.call_tool("remember", {**kwargs, "capture": capture_id})
        text = "\n".join(getattr(c, "text", "") for c in result.content)
        if result.is_error:
            raise ToolError(text)  # the model sees the server's error and can retry
        return text

    return beta_async_tool(
        remember, name="remember", description=tool.description, input_schema=tool.input_schema
    )


@asynccontextmanager
async def memento(url: str, token: str):
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=60) as http,
        Client(streamable_http_client(url, http_client=http)) as session,
    ):
        yield session


async def call(session, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    if result.is_error:
        raise RuntimeError(f"{name}: {result.content[0].text}")
    return result.structured_content


async def distil_note(llm, session, listed, capture: dict, current: list, now: str) -> dict:
    runner = llm.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        max_iterations=MAX_ITERATIONS,
        cache_control={"type": "ephemeral"},
        system=instructions(),
        tools=tools_for(listed, session, capture["id"]),
        messages=[{"role": "user", "content": note_message(capture, current, now)}],
    )
    final = await runner.until_done()
    said = " ".join(b.text for b in final.content if b.type == "text").strip()
    return {"stop": final.stop_reason, "said": said, "usage": final.usage}


async def run(url: str, token: str, since: datetime | None, dry_run: bool, log=None) -> int:
    log = log or say
    failures = 0
    async with memento(url, token) as session:
        start = since or datetime.now(UTC) - WINDOW
        box = await call(session, "inbox", {"received_since": start.isoformat(), "limit": 50})
        notes = box["captures"]
        log(f"{len(notes)} note(s) received since {start:%Y-%m-%d %H:%M}; "
            f"{box['waiting']} waiting in all")  # fmt: skip
        if dry_run or not notes:
            return 0
        listed = (await session.list_tools()).tools
        llm = anthropic.AsyncAnthropic()
        for capture in notes:
            try:
                out = await distil_note(
                    llm, session, listed, capture, box["current_entries"], box["now"]
                )
                use = out["usage"]
                log(f"{capture['id']}: {out['said'] or out['stop']} [in {use.input_tokens}, "
                    f"cached {use.cache_read_input_tokens}, out {use.output_tokens}]")  # fmt: skip
            except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
                failures += 1  # the next run's window still covers this note
                log(f"{capture['id']}: model call failed: {e!r}")
    return 1 if failures else 0


def say(line: str) -> None:
    print(line, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--since", type=datetime.fromisoformat,
                        help="Look back to this time instead of the last 35 minutes.")  # fmt: skip
    parser.add_argument("--dry-run", action="store_true", help="List the notes; call no model.")
    args = parser.parse_args()
    url, token = os.environ.get("MEMENTO_URL"), os.environ.get("MEMENTO_TOKEN")
    if not (url and token):
        say("Set MEMENTO_URL and MEMENTO_TOKEN (make client USER=<you> NAME=distiller).")
        return 2
    return asyncio.run(run(url, token, args.since, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
