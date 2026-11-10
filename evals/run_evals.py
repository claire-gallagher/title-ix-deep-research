"""Evaluate each part of the pipeline on its own, then the whole thing. Runs in-process (no server).

  uv run evals/run_evals.py plan        the Lead alone: clarify when it should? the right researchers?
                                         (one model call per case: cheap)
  uv run evals/run_evals.py data        the Data Researcher alone, on tasks with gold answers computed
                                         from the database: right numbers? queries, errors, tokens
  uv run evals/run_evals.py e2e         whole runs: clarify vs. report, gold numbers in the report,
                                         required mentions, forbidden claims, citations that resolve
  uv run evals/run_evals.py e2e --ids readme-trend,vague-1     only some cases

Because each part has its own eval, a failure points at the part to fix: a wrong plan is the Lead's
prompt; a wrong number with a good plan is the Data Researcher (schema, notes, prompt); a right
finding missing from the report is the Writer. Results: evals/results/<kind>-<time>.jsonl.
"""

import asyncio
import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from agents import set_tracing_disabled  # noqa: E402

set_tracing_disabled(True)

import eada  # noqa: E402
import research  # noqa: E402
from context_panel import AgentContexts  # noqa: E402
from scoring import score  # noqa: E402


def load(name: str, ids: set[str] | None = None) -> list[dict]:
    cases = [json.loads(line) for line in (HERE / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    return [c for c in cases if not ids or c["id"] in ids]


def resolve_gold(case: dict) -> dict:
    """Fill each fact's value from its SQL: gold answers are computed from the database."""
    for fact in case.get("facts", []):
        if "sql" in fact and fact.get("value") is None:
            result = eada.run_sql(fact["sql"])
            fact["value"] = result["rows"][0][0] if result.get("rows") else None
    return case


def new_state() -> research.Research:
    return research.Research(hooks=research.ResearchHooks(AgentContexts(), lambda e: None))


def tokens(state: research.Research) -> int:
    return sum(u["input_tokens"] + u["output_tokens"] for u in state.usage.values())


# --- The Lead alone ---


async def eval_plan(case: dict) -> dict:
    """A case without findings tests the plan (clarify? which researchers?). A case with findings
    tests the review: does the Lead go back for a real gap, and stop when the findings answer it?"""
    state = new_state()
    if "findings" in case:
        state.findings = [{"id": f"F{i}", "claim": c, "period_covered": None} for i, c in enumerate(case["findings"], 1)]
        state.gaps = case.get("gaps", [])
        plan = await research.review(case["question"], case.get("sent", {}), state, 2)
    else:
        plan = await research.make_plan(case["question"], state)
    kinds = set(research.plan_tasks(plan))
    if "findings" in case:  # review: follow up exactly when it should, with the right researchers
        ok = bool(kinds) == case["follow_up"] and set(case.get("need", [])) <= kinds and not (set(case.get("avoid", [])) & kinds)
        return {"id": case["id"], "ok": ok, "follow_up": bool(kinds), "kinds": sorted(kinds), "plan": plan.model_dump()}
    clarified = bool(plan.clarify) and not kinds
    ok = clarified == case["clarify"]
    if not case["clarify"]:
        ok = ok and set(case.get("need", [])) <= kinds and not (set(case.get("avoid", [])) & kinds)
    return {"id": case["id"], "ok": ok, "clarified": clarified, "kinds": sorted(kinds), "plan": plan.model_dump()}


# --- The Data Researcher alone ---


async def eval_data(case: dict) -> dict:
    case = resolve_gold(case)
    state = new_state()
    started = time.time()
    await research.run_researcher("data", case["task"], state)
    text = "\n".join(f"{f['claim']} | {f.get('evidence_text', '')}" for f in state.findings)
    s = score(case, text)
    calls = state.hooks.tool_calls
    return {
        "id": case["id"], "ok": s["ok"], "facts": s["facts"],
        "queries": sum(c["name"] == "run_sql" for c in calls),
        "errors": sum(c["name"] == "run_sql" and c["result"].startswith('{"error"') for c in calls),
        "verified": len(state.findings), "rejected": len(state.rejected),
        "tokens": tokens(state), "seconds": round(time.time() - started, 1),
        "gold": {f["label"]: f["value"] for f in case.get("facts", [])},
        "findings": [f["claim"] for f in state.findings],
        # What to look at when it fails: the queries, and why the evidence check threw findings out
        "sql": [c["args"].get("query") for c in calls if c["name"] == "run_sql" and isinstance(c["args"], dict)],
        "rejected_reasons": [f"{r['reason']} | claim: {r['claim']} | evidence: {r['evidence_text']}" for r in state.rejected],
    }


# --- Whole runs ---


def citations_ok(markdown: str) -> bool:
    """Every [n] in the body has a Sources entry, numbered 1..n without gaps; no raw [F#] left."""
    import re

    body, _, sources = markdown.partition("## Sources")
    cited = set(re.findall(r"\[(\d+)\]", body))
    listed = re.findall(r"\*\*\[(\d+)\]\*\*", sources)
    return (cited <= set(listed) and listed == [str(i) for i in range(1, len(listed) + 1)]
            and not re.search(r"\[F\d+", body))


async def eval_e2e(case: dict) -> dict:
    case = resolve_gold(case)
    hooks = research.ResearchHooks(AgentContexts(), lambda e: None)
    started = time.time()
    result = await research.deep_research(case["question"], hooks)
    state = result.get("state")
    out = {"id": case["id"], "expect": case["expect"], "got": result["type"],
           "seconds": round(time.time() - started, 1), "tokens": tokens(state) if state else None}
    if case["expect"] == "clarify" or (case["expect"] == "report_or_clarify" and result["type"] == "clarify"):
        # report_or_clarify: for a question no source can answer, saying so and offering what can be
        # answered is as good as a report; the required points and forbidden claims still apply
        s = score(case, result["markdown"])
        out["ok"] = result["type"] == "clarify" and not s["missing_mentions"] and not s["violations"]
        out["clarifying_question"] = result["markdown"]
        return out
    body = result["markdown"].partition("## Sources")[0]
    s = score(case, body)
    out.update(s)
    out["citations_ok"] = citations_ok(result["markdown"])
    out["has_limits"] = "### limits" in body.lower()
    out["ok"] = result["type"] == "report" and s["ok"] and out["citations_ok"] and out["has_limits"]
    out["markdown"] = result["markdown"]
    return out


RUNNERS = {"plan": ("plan_cases.jsonl", eval_plan), "data": ("data_cases.jsonl", eval_data), "e2e": ("e2e_cases.jsonl", eval_e2e)}


async def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in RUNNERS else None
    if not kind:
        print(__doc__)
        return
    ids = set(sys.argv[sys.argv.index("--ids") + 1].split(",")) if "--ids" in sys.argv else None
    file, run = RUNNERS[kind]
    results = []
    for case in load(file, ids):  # one at a time: parallel runs hit Vertex's rate limit
        try:
            r = await run(case)
        except Exception as e:  # one failing case shouldn't lose the others
            r = {"id": case["id"], "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        results.append(r)
        print(json.dumps({k: v for k, v in r.items() if k not in ("markdown", "plan", "findings")}, default=str))
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    path = out / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    path.write_text("\n".join(json.dumps(r, default=str) for r in results), encoding="utf-8")
    n = len(results)
    passed = sum(r["ok"] for r in results)
    print(f"\n{kind}: {passed}/{n} passed ({passed / n:.0%}, ±{1 / math.sqrt(n):.0%} at this sample size). Details: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
