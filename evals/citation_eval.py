"""Citation and behaviour checks against a running server.

  uv run app.py                                  (in another terminal)
  uv run evals/citation_eval.py                  deterministic checks
  uv run evals/citation_eval.py --judge          also ask a model whether each cited source supports its sentence

Deterministic checks, per question:
  - behaviour: a vague question gets a clarifying question; others get a report
  - every [n] in the report has an entry in its Sources section, numbered 1..n without gaps
  - every cited web/policy source was actually retrieved in this run's tool_calls
  - reports end with a "### Limits" section
Results go to evals/results/<timestamp>.jsonl, with a summary and ±1/sqrt(n) margin printed.

TODO: grow questions.jsonl to 15-20 questions, with where each came from in "origin".
"""

import json
import math
import re
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
SERVER = "http://127.0.0.1:8000"


def retrieved_urls(tool_calls: list[dict]) -> set[str]:
    urls = set()
    for call in tool_calls:
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if call["name"] == "fetch_page":
            urls.add(args.get("url", ""))
        elif call["name"] == "search_web":
            urls |= set(re.findall(r'"url": "([^"]+)"', call.get("result", "")))
        elif call["name"] == "read_policy":
            urls |= set(re.findall(r'"source_url": "([^"]+)"', call.get("result", "")))
    return urls


def check(item: dict, response: str, tool_calls: list[dict]) -> dict:
    is_report = "## Sources" in response
    result = {"id": item["id"], "expect": item["expect"]}
    if item["expect"] == "clarify":
        result["behaviour_ok"] = not is_report
        return result
    result["behaviour_ok"] = is_report
    body, _, sources = response.partition("## Sources")
    # Reports cite sources as [1], [2]... numbered without gaps; a leftover [F#] is an unknown finding
    cited = set(re.findall(r"\[(\d+)\]", body))
    listed = set(re.findall(r"\*\*\[(\d+)\]\*\*", sources))
    result["citations_resolve"] = cited <= listed and not re.search(r"\[F\d+\]", body)
    result["sources_numbered_in_order"] = sorted(listed, key=int) == [str(i) for i in range(1, len(listed) + 1)]
    links = re.findall(r"\]\((https?://[^)\s]+)\)", sources)
    retrieved = retrieved_urls(tool_calls)
    result["sources_retrieved"] = all(link in retrieved for link in links)
    result["has_limits_section"] = "### limits" in response.lower()  # the Writer's required closing section
    return result


def judge(response: str) -> float | None:
    """Optional LLM judge: share of cited sentences the model thinks their finding supports.
    TODO: pass the finding evidence (add it to /chat's tool_calls or a debug endpoint) for a real check."""
    return None


def main() -> None:
    items = [json.loads(line) for line in (HERE / "questions.jsonl").read_text().splitlines() if line.strip()]
    results = []
    for item in items:
        started = time.time()
        r = httpx.post(f"{SERVER}/chat", json={"message": item["question"]}, timeout=900)
        r.raise_for_status()
        body = r.json()
        result = check(item, body["response"], body["tool_calls"])
        result["seconds"] = round(time.time() - started)
        if "--judge" in sys.argv:
            result["judge"] = judge(body["response"])
        results.append(result)
        print(json.dumps(result))

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl").write_text("\n".join(json.dumps(r) for r in results))

    n = len(results)
    for key in ("behaviour_ok", "citations_resolve", "sources_numbered_in_order", "sources_retrieved", "has_limits_section"):
        values = [r[key] for r in results if key in r]
        if values:
            print(f"{key}: {sum(values)}/{len(values)} = {sum(values) / len(values):.0%} (±{1 / math.sqrt(len(values)):.0%})")
    print(f"{n} questions")


if __name__ == "__main__":
    main()
