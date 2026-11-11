"""The README examples: this agent vs. Gemini with Google Search, scored against the same gold facts.

  uv run evals/compare_baseline.py              the cases marked "readme" in e2e_cases.jsonl
  uv run evals/compare_baseline.py --reuse-agent   rerun only the baseline, with the agent's answers
                                                   from the latest results file
  uv run evals/compare_baseline.py --no-search     baseline without Google Search (no tools at all)
  uv run evals/compare_baseline.py --ids a,b --from-e2e   other e2e cases, reusing the agent's answers
                                                   from evals/results/e2e-*.jsonl -> docs/examples-data.md

Baseline: the same model the Lead and Writer use (Gemini 3.5 Flash), asked the bare question with
Google Search grounding on, so it can look things up too. Both answers are scored with
evals/scoring.py: gold numbers stated (computed from the database or quoted from the policy
documents), required points mentioned, forbidden claims avoided. Writes docs/examples.md (every
answer in full, for the README to link) and evals/results/baseline-<time>.json.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from run_evals import citations_ok, load, resolve_gold  # noqa: E402  (also sets up paths, .env, tracing)

import litellm  # noqa: E402
import research  # noqa: E402
from context_panel import AgentContexts  # noqa: E402
from scoring import score  # noqa: E402

BASELINE_MODEL = "vertex_ai/gemini-3.5-flash"
SEARCH = "--no-search" not in sys.argv
LABEL = "Gemini + Google Search" if SEARCH else "Gemini, no tools"


async def baseline(question: str, attempt: int = 1) -> dict:
    started = time.time()
    response = await litellm.acompletion(
        model=BASELINE_MODEL,
        messages=[{"role": "user", "content": question}],
        # Vertex AI grounding with Google Search; --no-search asks the bare model instead
        **({"tools": [{"googleSearch": {}}]} if SEARCH else {}),
        vertex_location="global",
        num_retries=5,
        timeout=120,
    )
    usage = response.usage
    grounding = getattr(response, "vertex_ai_grounding_metadata", None) or []
    web_sources = sum(len(g.get("groundingChunks", []) or []) for g in grounding if isinstance(g, dict))
    answer = response.choices[0].message.content or ""
    if not answer.strip() and attempt < 3:  # grounding occasionally returns nothing: don't score an empty answer
        return await baseline(question, attempt + 1)
    return {"answer": answer, "attempts": attempt, "seconds": round(time.time() - started, 1),
            "tokens": (usage.prompt_tokens or 0) + (usage.completion_tokens or 0), "web_sources": web_sources}


async def agent(question: str, clarify_answer: str | None = None) -> dict:
    started = time.time()
    hooks = research.ResearchHooks(AgentContexts(), lambda e: None)
    result = await research.deep_research(question, hooks)
    asked = None
    # A clarifying question isn't an answer: reply the way a user on the page would (app.compose)
    if result["type"] == "clarify" and clarify_answer:
        asked = result["markdown"]
        hooks = research.ResearchHooks(AgentContexts(), lambda e: None)
        result = await research.deep_research(
            f"{question}\n\nClarifying question: {asked}\nUser's answer: {clarify_answer}", hooks)
    state = result["state"]
    return {"answer": result["markdown"], "asked": asked, "seconds": round(time.time() - started, 1),
            "tokens": sum(u["input_tokens"] + u["output_tokens"] for u in state.usage.values()),
            "model_calls": sum(u["requests"] for u in state.usage.values()),
            "citations_ok": citations_ok(result["markdown"]), "sources": len(result.get("sources", []))}


def verdict(s: dict) -> str:
    parts = [f"{s['facts_found']}/{s['facts_total']} gold numbers"]
    parts.append("required points ✓" if not s["missing_mentions"] else f"missing: {', '.join(g[0] for g in s['missing_mentions'])}")
    parts.append("no forbidden claims ✓" if not s["violations"] else f"{len(s['violations'])} forbidden claim(s)")
    return "; ".join(parts)


def fmt(v, unit):
    if unit == "%":
        return f"{v:.1f} points"
    if unit == "count":
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return f"${v:,.0f}"


async def main() -> None:
    ids = set(sys.argv[sys.argv.index("--ids") + 1].split(",")) if "--ids" in sys.argv else None
    cases = [resolve_gold(c) for c in load("e2e_cases.jsonl") if (c["id"] in ids if ids else c.get("readme"))]
    rows, results = [], []
    previous = {}
    if "--reuse-agent" in sys.argv:
        latest = sorted((HERE / "results").glob("baseline-*.json"))[-1]
        previous = {r["id"]: r["agent"] for r in json.loads(latest.read_text(encoding="utf-8"))}
    if "--from-e2e" in sys.argv:
        # The agent's answers from e2e runs (latest answer per case wins): only Gemini is called
        for path in sorted((HERE / "results").glob("e2e-*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                if r.get("markdown") and r.get("got") == "report":
                    previous[r["id"]] = {"answer": r["markdown"], "asked": None, "seconds": r["seconds"],
                                         "tokens": r.get("tokens") or 0,
                                         "sources": r["markdown"].partition("## Sources")[2].count("- **[")}
    for case in cases:
        a = previous.get(case["id"]) or await agent(case["question"], case.get("clarify_answer"))
        a.pop("score", None)
        b = await baseline(case["question"])
        sa = score(case, a["answer"].partition("## Sources")[0])
        sb = score(case, b["answer"])
        results.append({"id": case["id"], "question": case["question"], "clarify_answer": case.get("clarify_answer"), "facts": case.get("facts", []),
                        "agent": {**a, "score": sa}, "baseline": {**b, "score": sb}})
        print(json.dumps({"id": case["id"], "agent": verdict(sa), "baseline": verdict(sb)}, ensure_ascii=False))

    stamp = time.strftime("%Y-%m-%d")
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / f"baseline-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")

    md = [f"# Examples: this agent vs. {LABEL}\n",
          f"Generated by `uv run evals/compare_baseline.py` on {stamp}. Baseline: {BASELINE_MODEL}{' with Google Search grounding' if SEARCH else ', no tools'}, "
          "asked the bare question. Both scored by `evals/scoring.py` against the same gold "
          "facts: numbers computed from the EADA database (the SQL is in `evals/e2e_cases.jsonl`) or quoted from "
          "the policy documents.\n"]
    for r in results:
        md.append(f"\n## {r['question']}\n")
        if r["facts"]:
            md.append("| Gold fact | Value | From |\n|---|---|---|")
            for f in r["facts"]:
                md.append(f"| {f['label']} | {fmt(f['value'], f.get('unit'))} | {f.get('source') or 'EADA database (SQL in evals/e2e_cases.jsonl)'} |")
        md.append(f"\n| | This agent | {LABEL} |\n|---|---|---|")
        md.append(f"| Score | {verdict(r['agent']['score'])} | {verdict(r['baseline']['score'])} |")
        md.append(f"| Time, tokens | {r['agent']['seconds']} s, {r['agent']['tokens']:,} | {r['baseline']['seconds']} s, {r['baseline']['tokens']:,} |")
        md.append(f"| Sources | {r['agent']['sources']} cited, every one retrieved this run | {r['baseline']['web_sources']} search results (not linked to sentences) |")
        for who in ("agent", "baseline"):
            v = r[who]["score"]["violations"]
            if v:
                md.append(f"\nForbidden claim{'s' if len(v) > 1 else ''} ({'agent' if who == 'agent' else 'baseline'}): " + " / ".join(f"“{s}”" for s in v))
        md.append("\n### This agent\n")
        if r["agent"].get("asked"):
            md.append(f"> **Asked first:** {r['agent']['asked']}  \n> **Answer given:** {r['clarify_answer']}\n")
        md.append(r["agent"]["answer"])
        md.append(f"\n### {LABEL}\n")
        md.append(r["baseline"]["answer"])
        md.append("\n---")
    (ROOT / "docs").mkdir(exist_ok=True)
    name = "examples-data.md" if ids else ("examples.md" if SEARCH else "examples-no-search.md")
    (ROOT / "docs" / name).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Wrote docs/{name} and evals/results/baseline-*.json")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
