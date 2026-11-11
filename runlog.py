"""A trace of every research run, and the Tavily credits it spent. Stdlib only.

deep_research() calls save() at the end of each run, finished or failed. Each run is one JSON file
in data/cache/runs/ (gitignored; /tmp/cache/runs on Cloud Run, where the one-line summary printed
to stdout is what Cloud Logging keeps): the question, every tool call with the agent that made it,
the findings that passed the evidence check, the ones that didn't and why, searches (live vs.
cached), and tokens per agent.

  uv run runlog.py            recent runs, one line each
  uv run runlog.py last       the latest run in detail (or: runlog.py <file name prefix>)
  uv run runlog.py tavily     Tavily credits used / left this billing cycle (costs no credit)
"""

import json
import sys
import time
import uuid
from pathlib import Path

import web

RUNS_DIR = web.CACHE_DIR.parent / "runs"
TOOL_RESULT_CHARS = 1_500  # enough to see what came back without storing whole pages twice


def save(question: str, state, result: dict, started: float) -> Path | None:
    """Write one run's trace. Never raises: a logging failure mustn't lose the user's answer."""
    try:
        tool_calls = [{**c, "result": str(c.get("result", ""))[:TOOL_RESULT_CHARS]} for c in state.hooks.tool_calls]
        totals = {k: sum(u[k] for u in state.usage.values()) for k in ("requests", "input_tokens", "output_tokens")}
        summary = {
            "outcome": result.get("type"),
            "seconds": round(time.time() - started, 1),
            "researchers": state.researchers,
            "sql_queries": state.queries,
            "searches_live": state.searches - state.searches_cached,  # = Tavily credits spent
            "searches_cached": state.searches_cached,
            "pages_fetched": sum(c["name"] == "fetch_page" for c in state.hooks.tool_calls),
            "findings_verified": len(state.findings),
            "findings_rejected": len(state.rejected),
            # Claims the query-fit check rejected, on the first try or the repair (each claim once).
            # A rising share on questions with sound answers means evidence.fits_task is too strict.
            "query_fit_rejected": len({x.get("claim") for x in [*state.rejected, *(f for r in getattr(state, "repairs", []) for f in r["first_try"])]
                                       if x.get("check") == "query_fit"}),
            **totals,
        }
        run = {
            "run_id": uuid.uuid4().hex[:8],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "question": question,
            "plans": getattr(state, "plans", []),
            "summary": summary,
            "usage_by_agent": state.usage,
            "findings": [{k: f.get(k) for k in ("id", "researcher", "claim", "source_url", "source_type", "access", "evidence_text", "check")} for f in state.findings],
            "rejected": state.rejected,
            "repairs": getattr(state, "repairs", []),
            "scopes": getattr(state, "scopes", []),  # what each data task's queries were checked against
            "gaps": state.gaps,
            "citation_check": result.get("citation_check"),
            "tool_calls": tool_calls,
            "markdown": result.get("markdown", ""),
        }
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = RUNS_DIR / f"{run['at'].replace(':', '')}-{run['run_id']}.json"
        path.write_text(json.dumps(run, indent=1, default=str), encoding="utf-8")
        print(json.dumps({"research_run": run["run_id"], "question": question[:120], **summary}), flush=True)
        return path
    except Exception as e:  # noqa: BLE001
        print(f"runlog: could not save the run trace: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _runs() -> list[Path]:
    return sorted(RUNS_DIR.glob("*.json")) if RUNS_DIR.exists() else []


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(n: int = 15) -> None:
    runs = _runs()[-n:]
    if not runs:
        print(f"No runs yet in {RUNS_DIR}.")
        return
    print(f"{'file':<28} {'outcome':<8} {'sec':>5} {'ok':>3} {'rej':>3} {'fit':>3} {'web$':>4} {'cache':>5} {'tokens':>8}  question")
    for path in runs:
        r = _load(path)
        s = r["summary"]
        tokens = s.get("input_tokens", 0) + s.get("output_tokens", 0)
        print(f"{path.stem[:28]:<28} {s['outcome'] or '':<8} {s['seconds']:>5} {s['findings_verified']:>3} "
              f"{s['findings_rejected']:>3} {s.get('query_fit_rejected', 0):>3} {s['searches_live']:>4} {s['searches_cached']:>5} "
              f"{tokens:>8}  {r['question'][:60]}")
    every = [_load(p)["summary"] for p in _runs()]
    live = sum(s["searches_live"] for s in every)
    fit = [s for s in every if "query_fit_rejected" in s]
    print(f"\n{len(every)} runs logged; {live} live Tavily searches among them (web$ = credits spent).")
    if fit:
        # Per verified finding, so busier runs don't look worse: if this climbs, check `runlog.py <file>`
        # for sound findings fits_task turned away before tightening prompts further
        rate = sum(s["query_fit_rejected"] for s in fit) / max(1, sum(s["findings_verified"] for s in fit))
        print(f"fit = claims rejected by the query-fit check: {rate:.0%} of verified findings over {len(fit)} runs.")


_REPEAT_MARKERS = ("already ran this exact query", "same rows as", "Query limit reached", "Search limit reached")


def scorecard(run: dict) -> dict[str, dict]:
    """Per agent: model calls and tokens, tool calls, failed calls, refused repeats, findings kept and
    rejected. Each agent has its own job, so each gets its own numbers: this is what tells you
    which one to fix. Built from the trace, so it works on old runs too."""
    cards: dict[str, dict] = {}
    for agent, u in run["usage_by_agent"].items():
        cards[agent] = {"calls": u["requests"], "input_tokens": u["input_tokens"], "output_tokens": u["output_tokens"],
                        "tools": 0, "errors": 0, "repeats": 0, "ok": 0, "rejected": 0}
    blank = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "tools": 0, "errors": 0, "repeats": 0, "ok": 0, "rejected": 0}
    for c in run["tool_calls"]:
        card = cards.setdefault(c["agent"], dict(blank))
        card["tools"] += 1
        result = str(c.get("result", ""))
        if result.startswith('{"error"'):
            card["repeats" if any(m in result for m in _REPEAT_MARKERS) else "errors"] += 1
    for f in run["findings"]:
        cards.setdefault(f.get("researcher") or "?", dict(blank))["ok"] += 1
    for x in run["rejected"]:
        cards.setdefault(x.get("researcher") or "?", dict(blank))["rejected"] += 1
    return cards


def show(path: Path) -> None:
    r = _load(path)
    s = r["summary"]
    print(f"# {r['at']}  {path.name}\nQuestion: {r['question']}\n")
    print("Summary: " + ", ".join(f"{k}={v}" for k, v in s.items()))
    # Round 1 is the Lead's plan; later rounds are its reviews (no tasks = it decided to stop)
    for p in r.get("plans") or ([{"round": 1, **r["plan"]}] if r.get("plan") else []):
        tasks = {k: v for k, v in p.items() if v and k not in ("round",)}
        label = "Lead's plan" if p["round"] == 1 else f"Lead's review, round {p['round']}"
        print(f"\n{label}:" + ("" if tasks else " no follow-up needed"))
        for k, v in tasks.items():
            print(f"  {k}: {v}")
    print("\nScorecard by agent: what each one cost and what it produced")
    print(f"  {'agent':<22} {'calls':>5} {'in tok':>8} {'out':>6} {'tools':>5} {'errors':>6} {'repeats':>7} {'ok':>3} {'rej':>3}")
    for agent, card in scorecard(r).items():
        print(f"  {agent:<22} {card['calls']:>5} {card['input_tokens']:>8} {card['output_tokens']:>6} {card['tools']:>5} "
              f"{card['errors']:>6} {card['repeats']:>7} {card['ok']:>3} {card['rejected']:>3}")
    print("  errors: tool calls that failed (bad SQL, unknown column...); repeats: re-runs refused as duplicates")
    print("\nTool calls:")
    for c in r["tool_calls"]:
        args = c["args"] if isinstance(c["args"], str) else json.dumps(c["args"], default=str)
        print(f"  {c['agent']:<22} {c['name']:<16} {args[:110]}")
    print(f"\nVerified findings ({len(r['findings'])}):")
    for f in r["findings"]:
        print(f"  [{f['id']}] {f['source_url'][:70]}  ({f['access']})  {f['claim'][:110]}")
    print(f"\nRejected findings ({len(r['rejected'])}): the evidence check's reason, then the claim")
    for x in r["rejected"]:
        print(f"  {x.get('researcher', '')}: {x['reason']}\n      claim: {(x.get('claim') or '')[:120]}\n      source: {x.get('source_url')}")
        if x.get("evidence_text"):
            print(f"      evidence: {x['evidence_text'][:160]!r}")
    for rep in r.get("repairs", []):
        print(f"\nRepair turn ({rep['researcher']}): {rep['repaired']} of {len(rep['first_try'])} first-try rejections fixed")
        for x in rep["first_try"]:
            print(f"  was: {x['reason']} | {(x.get('claim') or '')[:110]}")
    if r.get("gaps"):
        print("\nGaps reported:\n" + "\n".join(f"  - {g}" for g in r["gaps"]))
    if r.get("citation_check"):
        print(f"\nCitation check: {r['citation_check']}")


def tavily() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
    usage = web.tavily_usage()
    if "error" in usage:
        print(f"Tavily usage: {usage['error']}")
        return
    key, account = usage.get("key", {}), usage.get("account", {})
    print(f"This API key: {key.get('usage')} credits used of {key.get('limit') or 'no key limit'} "
          f"(searches: {key.get('search_usage')})")
    print(f"Account ({account.get('current_plan')}): {account.get('plan_usage')} of {account.get('plan_limit')} plan credits used"
          + (f", pay-as-you-go {account.get('paygo_usage')} of {account.get('paygo_limit')}" if account.get("paygo_limit") else ""))
    live = sum(_load(p)["summary"]["searches_live"] for p in _runs())
    print(f"Logged runs on this machine spent {live} search credits (cached searches are free).")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # report text has curly quotes; Windows consoles default to cp1252
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "tavily":
        tavily()
    elif arg == "last":
        runs = _runs()
        show(runs[-1]) if runs else print(f"No runs yet in {RUNS_DIR}.")
    elif arg:
        match = [p for p in _runs() if p.name.startswith(arg)]
        show(match[-1]) if match else print(f"No run file starting with {arg!r} in {RUNS_DIR}.")
    else:
        list_runs()
