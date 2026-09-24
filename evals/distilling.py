"""
Distiller evals (M7): the capture-policy cases, arriving as inbox notes.

Each case's message is saved with `raw_text` alone, so it waits in the inbox like
a typed or spoken note, and the distiller runs once over it with its own token
(read and write, never forget). The stored entries are graded by the same grader
as live clients, with two differences for a client nobody is watching (0019):
where the live policy says "ask", the right answer is to save nothing and leave
the note open; and there is no receipt to give.

Like the distiller, this is a client: it never imports `memories` or `config`.
It runs in the distiller's environment, because it drives the distiller itself.

    cd distiller && uv run python ../evals/distilling.py --runs 3
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1] / "distiller")]

import capture as c  # noqa: E402  (the capture harness: server, seeding, grader)

import distil  # noqa: E402

# Live-conversation cases with no inbox equivalent: an undo of a save made moments
# ago, a client whose own calls were refused, a client that isn't told the date.
NOT_FOR_THE_INBOX = {"undo", "weak-client-escape", "no-date-knowledge"}

NEW_USER = """
import json
from django.contrib.auth import get_user_model
from memories import services
from memories.models import Profile, Scope
user = get_user_model().objects.create(username={name!r})
Profile.objects.create(owner=user, timezone="Europe/London")
_, model = services.create_client(user, "distiller", scopes=[Scope.READ, Scope.WRITE])
_, harness = services.create_client(user, "eval-harness", scopes=[Scope.READ, Scope.WRITE])
print(json.dumps({{"model": model, "harness": harness}}))
"""


def new_user(name: str) -> dict:
    out = c.manage("shell", stdin=NEW_USER.format(name=name))
    return json.loads(out.strip().splitlines()[-1])


def state(tokens: dict, seeded: set[str], note_id: str) -> dict:
    recall, inbox = asyncio.run(
        c.mcp_call(tokens["harness"], [("recall", {"view": "all", "limit": 100}), ("inbox", {})])
    )
    return {
        "entries": [e for e in recall["entries"] if e["id"] not in seeded],
        "seeded_left": [e["id"] for e in recall["entries"] if e["id"] in seeded],
        "captures": [],  # the note itself is not a save
        "left_open": any(n["id"] == note_id for n in inbox["captures"]),
    }


def grade(case: dict, st: dict, today) -> dict:
    expect, wrote = case["expect"], bool(st["entries"])
    if expect == "ask" or (expect == "save_or_ask" and not wrote):
        fails = []
        if wrote:
            fails.append(("policy", "saved what it should have left for the user"))
        if not st["left_open"]:
            fails.append(("policy", "closed a note it should have left for the user"))
        return {"passed": not fails, "notes": [], "saves": len(st["entries"]),
                "fails": [{"category": k, "detail": d} for k, d in fails]}  # fmt: skip
    result = c.grade(case | {"expect": expect.replace("save_or_ask", "save")},
                     {"calls": [], "final": ""}, st, today, receipts=False)  # fmt: skip
    if expect == "save" and wrote and st["left_open"]:
        result["notes"].append("saved entries but left the note open")
    if expect == "skip" and st["left_open"]:
        result["notes"].append("left the note open instead of dismissing it")
    return result


def run_one(case: dict, n: int, run_id: str) -> dict:
    try:
        return _run_one(case, n, run_id)
    except Exception as e:  # one broken run must not lose the others
        return {"case": case["id"], "run": n, "passed": False, "model": distil.MODEL,
                "fails": [{"category": "harness", "detail": repr(e)}], "notes": []}  # fmt: skip


def _run_one(case: dict, n: int, run_id: str) -> dict:
    if case.get("requires_local_time"):
        lo, hi = case["requires_local_time"]
        if not (lo <= datetime.now(c.TZ).strftime("%H:%M") < hi):
            return {"case": case["id"], "run": n, "skipped": f"needs local time {lo}-{hi}"}
    tokens = new_user(f"distil-{run_id}-{case['id']}-{n}")
    ids = c.seed(case, tokens)
    (saved,) = asyncio.run(
        c.mcp_call(tokens["harness"], [("remember", {"raw_text": case["message"]})])
    )
    note_id = saved["inbox"]["id"]
    log: list[str] = []  # not redirect_stdout: that is process-wide, and runs are parallel
    code = asyncio.run(distil.run(c.URL, tokens["model"], None, False, log=log.append))
    today = datetime.now(c.TZ).date()
    st = state(tokens, set(ids.values()), note_id)
    result = grade(case, st, today)
    if code:
        result["fails"].append({"category": "harness", "detail": "distiller run failed"})
        result["passed"] = False
    return {"case": case["id"], "run": n, **result, "model": distil.MODEL,
            "log": log, "stored": st}  # fmt: skip


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--case", action="append", help="Only these case ids.")
    parser.add_argument("--parallel", type=int, default=4)
    args = parser.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY: the distiller calls the model directly.")

    cases = [
        x for x in json.loads(c.CASES.read_text())["cases"] if x["id"] not in NOT_FOR_THE_INBOX
    ]
    if args.case:
        cases = [x for x in cases if x["id"] in args.case]
    run_id = uuid.uuid4().hex[:6]
    server = c.start_server()
    try:
        jobs = [(x, n) for n in range(1, args.runs + 1) for x in cases]
        with ThreadPoolExecutor(args.parallel) as pool:
            results = list(pool.map(lambda j: run_one(j[0], j[1], run_id), jobs))
    finally:
        server.terminate()
    label = f"distiller-{distil.MODEL}"
    stem = f"{datetime.now(c.TZ):%Y-%m-%d}-{label}"
    c.RESULTS.mkdir(parents=True, exist_ok=True)
    (c.RESULTS / f"{stem}.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    (c.RESULTS / f"{stem}.md").write_text(c.report(results, label))
    print(c.report(results, label))


if __name__ == "__main__":
    sys.exit(main())
