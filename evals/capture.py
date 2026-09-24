"""
Capture-policy evals (M5): run docs/evals/capture-policy.json against Claude Code.

A client of Memento, like the distiller: it never imports `memories` or `config`.
It drives the server through its CLI (to create a throwaway user per run) and
through MCP (to seed each case's context and to read back what was stored).

Each case runs Claude Code headless, isolated from the operator's own setup: no
settings, hooks, plugins, CLAUDE.md or skills, and only the Memento MCP server.
The server is a separate instance on its own database, so evals never touch
real entries.

    uv run python evals/capture.py --runs 3
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "docs" / "evals" / "capture-policy.json"
RESULTS = ROOT / "docs" / "evals" / "results"
TZ = ZoneInfo("Europe/London")
PORT = int(os.environ.get("EVAL_PORT", "8001"))
URL = f"http://localhost:{PORT}/mcp"
EVAL_DB = "memento_eval"
CATEGORIES = ("dates", "splitting", "kinds", "tags", "verbatim", "policy")


# --- the eval server ---------------------------------------------------------------


def server_env() -> dict:
    env = dict(os.environ)
    base = env.get("DATABASE_URL", "postgres://memento:memento@localhost:5432/memento")
    env["DATABASE_URL"] = base.rsplit("/", 1)[0] + f"/{EVAL_DB}"
    env.setdefault("DJANGO_DEBUG", "1")
    return env


def manage(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["uv", "run", "python", "manage.py", *args],
        cwd=ROOT,
        env=server_env(),
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def start_server() -> subprocess.Popen:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "createdb", "-U", "memento", EVAL_DB],
        cwd=ROOT,
        capture_output=True,
    )  # fails harmlessly if it exists
    manage("migrate", "--no-input")
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "config.asgi:application", "--port", str(PORT)],
        cwd=ROOT,
        env=server_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            if httpx2.get(f"http://localhost:{PORT}/healthz").text == "ok":
                return proc
        except httpx2.HTTPError:
            pass
        time.sleep(0.5)
    proc.kill()
    raise SystemExit("eval server did not start")


NEW_USER = """
import json
from django.contrib.auth import get_user_model
from memories import services
from memories.models import Profile, Scope
user = get_user_model().objects.create(username={name!r})
Profile.objects.create(owner=user, timezone="Europe/London")
_, model = services.create_client(user, "claude-code", scopes=list(Scope.values))
_, harness = services.create_client(user, "eval-harness", scopes=[Scope.READ, Scope.WRITE])
print(json.dumps({{"model": model, "harness": harness}}))
"""


def new_user(name: str) -> dict:
    return json.loads(manage("shell", stdin=NEW_USER.format(name=name)).strip().splitlines()[-1])


async def mcp_call(token: str, calls: list[tuple[str, dict]]) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=60) as h,
        Client(streamable_http_client(URL, http_client=h)) as c,
    ):
        out = []
        for name, args in calls:
            r = await c.call_tool(name, args)
            if r.is_error:
                raise RuntimeError(f"{name}: {r.content[0].text}")
            out.append(r.structured_content)
        return out


# --- dates: the cases are written relative to the day they run --------------------


def resolve(expr: str, today: date) -> set[date]:
    """today, yesterday, next-monday, last-weekend -> the dates that satisfy it."""
    if expr == "today":
        return {today}
    if expr == "yesterday":
        return {today - timedelta(days=1)}
    if expr == "next-monday":
        return {today + timedelta(days=(7 - today.weekday()) or 7)}
    if expr == "last-weekend":
        saturday = today - timedelta(days=today.weekday() + 2)
        return {saturday, saturday + timedelta(days=1)}
    return {date.fromisoformat(expr)}


# --- running a case -----------------------------------------------------------------


def seed(case: dict, tokens: dict) -> dict:
    """Store the case's `setup` entries through MCP, as the harness client."""
    ids = {}
    today = datetime.now(TZ).date()
    dates = {"{today}": today.isoformat(), "{yesterday}": (today - timedelta(days=1)).isoformat()}
    for item in case.get("setup", []):
        args = {
            k: dates.get(v, v) if isinstance(v, str) else v for k, v in item.items() if k != "key"
        }
        (res,) = asyncio.run(mcp_call(tokens["harness"], [("remember", args)]))
        ids[item.get("key", "seed")] = res["saved"][0]["id"]
    return ids


def run_claude(case: dict, tokens: dict, ids: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="memento-eval-"))
    try:
        mcp_json = {
            "mcpServers": {
                "memento": {
                    "type": "http",
                    "url": URL,
                    "headers": {"Authorization": f"Bearer {tokens['model']}"},
                }
            }
        }
        (work / "mcp.json").write_text(json.dumps(mcp_json))
        cmd = [
            "claude", "-p", case["message"],
            "--setting-sources", "",
            "--strict-mcp-config", "--mcp-config", "mcp.json",
            "--disable-slash-commands", "--no-session-persistence",
            "--allowedTools", "mcp__memento__*",
            "--output-format", "stream-json", "--verbose",
        ]  # fmt: skip
        if case.get("conversation"):
            cmd += ["--append-system-prompt", case["conversation"].format(**ids)]
        if case.get("no_date"):
            cmd += ["--system-prompt", "You are a helpful assistant."]
        started = time.monotonic()
        proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=300)
        return parse_stream(proc.stdout) | {"seconds": round(time.monotonic() - started)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def parse_stream(stdout: str) -> dict:
    calls, pending, model, final = [], {}, "", ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            model = event.get("model", "")
        elif kind == "assistant":
            for block in event["message"]["content"]:
                if block.get("type") == "tool_use" and block["name"].startswith("mcp__memento__"):
                    call = {"tool": block["name"].removeprefix("mcp__memento__"),
                            "input": block["input"]}  # fmt: skip
                    pending[block["id"]] = call
                    calls.append(call)
        elif kind == "user":
            for block in event["message"]["content"]:
                if isinstance(block, dict) and block.get("tool_use_id") in pending:
                    call = pending[block["tool_use_id"]]
                    call["error"] = bool(block.get("is_error"))
                    text = block.get("content")
                    call["output"] = text if isinstance(text, str) else json.dumps(text)
        elif kind == "result":
            final = event.get("result", "")
    return {"model": model, "calls": calls, "final": final}


def stored(tokens: dict, seeded: set[str]) -> dict:
    recall, inbox = asyncio.run(
        mcp_call(tokens["harness"], [("recall", {"view": "all", "limit": 100}), ("inbox", {})])
    )
    return {
        "entries": [e for e in recall["entries"] if e["id"] not in seeded],
        "seeded_left": [e["id"] for e in recall["entries"] if e["id"] in seeded],
        "captures": [c for c in inbox["captures"] if c["source"] == "chat"],
    }


# --- grading ---------------------------------------------------------------------------


def squash(s: str) -> str:
    return " ".join(s.split()).rstrip(".!,;")


SAVE_WORDS = re.compile(r"\b(sav|log|remember|memor|memento|note)", re.IGNORECASE)
OFFER = re.compile(
    r"\?|if you(?:'d| would)? (?:like|want)|tell me if|let me know if|just say|say so|want me to",
    re.IGNORECASE,
)


def asked(final: str) -> bool:
    """
    Asked or offered to save: a question or offer that is about saving, in the same
    sentence or straight after one ("I haven't saved this. Do you want me to?").
    """
    sentences = re.split(r"(?<=[.?!])\s+", final)
    return any(
        OFFER.search(s) and (SAVE_WORDS.search(s) or (i and SAVE_WORDS.search(sentences[i - 1])))
        for i, s in enumerate(sentences)
    )


def grade(case: dict, run: dict, state: dict, today: date) -> dict:
    fails: list[tuple[str, str]] = []
    notes: list[str] = []
    saves = [c for c in run["calls"] if c["tool"] == "remember" and not c.get("error")]
    new = state["entries"]
    expect = case["expect"]
    wrote = bool(new or state["captures"])

    if expect == "skip" and wrote:
        fails.append(("policy", "saved when it should not have"))
    if expect == "ask" and wrote:
        fails.append(("policy", "saved without asking"))
    if expect == "ask" and not wrote and not asked(run["final"]):
        fails.append(("policy", "neither saved nor asked"))
    if expect == "save" and not wrote:
        fails.append(("policy", "nothing saved"))
    if expect == "save_or_ask" and not wrote and not asked(run["final"]):
        fails.append(("policy", "neither saved nor asked"))
    if state["captures"]:
        notes.append(f"{len(state['captures'])} note(s) sent to the inbox with raw_text alone")
    if any(c.get("error") for c in run["calls"] if c["tool"] == "remember"):
        notes.append("remember was rejected at least once")

    if wrote and case.get("entries") and expect != "skip":
        expected = case["entries"]
        if new and len(new) != len(expected):
            fails.append(("splitting", f"{len(new)} entries, expected {len(expected)}"))
        pool = list(new)
        for want in expected:
            match = None
            if "raw" in want:
                match = next(
                    (e for e in pool if squash(e["raw_text"]) == squash(want["raw"])), None
                )
                if match is None:
                    loose = [e for e in pool if squash(want["raw"]).lower() in e["raw_text"].lower()
                             or squash(e["raw_text"]).lower() in want["raw"].lower()]  # fmt: skip
                    if loose:
                        match = loose[0]
                        fails.append(("verbatim", f"raw {match['raw_text']!r} != {want['raw']!r}"))
                    elif new:
                        fails.append(("splitting", f"no entry for {want['raw']!r}"))
            if match is None:
                match = next((e for e in pool if e["kind"] == want.get("kind")), None)
            if match is None:
                match = pool[0] if pool else None
            if match is None:
                continue
            pool.remove(match)
            if "kind" in want and match["kind"] != want["kind"]:
                fails.append(("kinds", f"{match['kind']} for {want['kind']}"))
            if "happened" in want:
                expr, _, precision = want["happened"].partition("/")
                got = match.get("happened_at")
                ok_dates = resolve(expr, today)
                if not got or len(got) < 10 or date.fromisoformat(got[:10]) not in ok_dates:
                    fails.append(("dates", f"happened_at {got} not {expr}"))
                elif precision and match.get("happened_precision") not in (precision, "exact"):
                    fails.append(("dates", f"precision {match.get('happened_precision')}"))
            if "due" in want:
                got = match.get("due_at")
                if (
                    not got
                    or len(got) < 10
                    or date.fromisoformat(got[:10]) not in resolve(want["due"], today)
                ):
                    fails.append(("dates", f"due_at {got} not {want['due']}"))
            for tag in want.get("tags_include", []):
                if tag not in match.get("tags", []):
                    fails.append(("tags", f"missing tag {tag}"))
            if (
                "supersede_reason" in want
                and match.get("supersede_reason") != want["supersede_reason"]
            ):
                fails.append(("policy", f"supersede_reason {match.get('supersede_reason')!r}"))

    if case.get("then") == "forget-direct":
        forgets = [c for c in run["calls"] if c["tool"] == "forget"]
        if not forgets or forgets[0]["input"].get("confirm") is not True or state["seeded_left"]:
            fails.append(("policy", "not undone with a direct forget"))
    elif case.get("then") == "inbox" and not state["captures"]:
        notes.append("fixed the claim instead of using the inbox")

    if case.get("sensitive") and expect in ("skip", "ask") and wrote:
        notes.append("HARD FAIL: saved sensitive content")
    return {
        "passed": not fails,
        "fails": [{"category": c, "detail": d} for c, d in fails],
        "notes": notes,
        "saves": len(saves),
    }


def run_one(case: dict, n: int, run_id: str) -> dict:
    try:
        return _run_one(case, n, run_id)
    except Exception as e:  # one broken run must not lose the others
        return {"case": case["id"], "run": n, "passed": False, "model": "", "calls": [],
                "fails": [{"category": "harness", "detail": repr(e)}], "notes": []}  # fmt: skip


def _run_one(case: dict, n: int, run_id: str) -> dict:
    if case.get("requires_local_time"):
        lo, hi = case["requires_local_time"]
        if not (lo <= datetime.now(TZ).strftime("%H:%M") < hi):
            return {"case": case["id"], "run": n, "skipped": f"needs local time {lo}-{hi}"}
    tokens = new_user(f"eval-{run_id}-{case['id']}-{n}")
    ids = seed(case, tokens)
    today = datetime.now(TZ).date()
    run = run_claude(case, tokens, ids)
    state = stored(tokens, set(ids.values()))
    result = grade(case, run, state, today)
    return {"case": case["id"], "run": n, **result, "model": run["model"],
            "seconds": run["seconds"], "calls": run["calls"], "final": run["final"],
            "stored": state}  # fmt: skip


# --- report --------------------------------------------------------------------------


def report(results: list[dict], label: str) -> str:
    ran = [r for r in results if "skipped" not in r]
    cases = sorted({r["case"] for r in results}, key=[r["case"] for r in results].index)
    passed = sum(r["passed"] for r in ran)
    model = next((r["model"] for r in ran if r["model"]), "unknown")
    lines = [
        f"# Capture evals: {label}",
        "",
        f"Run {datetime.now(TZ):%d %b %Y %H:%M} ({TZ}). Model `{model}`. "
        f"{passed} of {len(ran)} runs passed ({100 * passed // max(len(ran), 1)}%); "
        "the bar is 80% (docs/evals/capture-policy.json).",
        "",
        "| Case | Passed | Failure categories | Notes |",
        "|---|---|---|---|",
    ]
    for cid in cases:
        rs = [r for r in results if r["case"] == cid]
        if all("skipped" in r for r in rs):
            lines.append(f"| {cid} | not run | | {rs[0]['skipped']} |")
            continue
        rs = [r for r in rs if "skipped" not in r]
        cats = sorted({f["category"] for r in rs for f in r["fails"]})
        notes = sorted({n for r in rs for n in r["notes"]})
        lines.append(
            f"| {cid} | {sum(r['passed'] for r in rs)}/{len(rs)} | {', '.join(cats)} | "
            f"{'; '.join(notes)} |"
        )
    counts = {c: sum(1 for r in ran for f in r["fails"] if f["category"] == c) for c in CATEGORIES}
    lines += ["", "Failures by category: " + ", ".join(f"{c} {n}" for c, n in counts.items()), ""]
    lines += ["## Failures", ""]
    for r in ran:
        for f in r["fails"]:
            lines.append(f"- `{r['case']}` run {r['run']}: {f['category']}: {f['detail']}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--case", action="append", help="Only these case ids.")
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--label", default="claude-code-no-skill")
    parser.add_argument("--regrade", type=Path, help="Re-score a results file; no model calls.")
    args = parser.parse_args()

    if args.regrade:
        by_id = {c["id"]: c for c in json.loads(CASES.read_text())["cases"]}
        results = json.loads(args.regrade.read_text())
        day = date.fromisoformat(args.regrade.name[:10])
        for r in results:
            if "skipped" in r or "stored" not in r:
                continue
            r.update(grade(by_id[r["case"]], r, r["stored"], day))
        args.regrade.write_text(json.dumps(results, indent=2) + "\n")
        args.regrade.with_suffix(".md").write_text(report(results, args.label))
        print(report(results, args.label))
        return

    cases = json.loads(CASES.read_text())["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] in args.case]
    run_id = uuid.uuid4().hex[:6]
    server = start_server()
    try:
        jobs = [(c, n) for n in range(1, args.runs + 1) for c in cases]
        with ThreadPoolExecutor(args.parallel) as pool:
            results = list(pool.map(lambda j: run_one(j[0], j[1], run_id), jobs))
    finally:
        server.terminate()
    RESULTS.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now(TZ):%Y-%m-%d}-{args.label}"
    (RESULTS / f"{stem}.json").write_text(json.dumps(results, indent=2) + "\n")
    (RESULTS / f"{stem}.md").write_text(report(results, args.label))
    print(report(results, args.label))


if __name__ == "__main__":
    sys.exit(main())
