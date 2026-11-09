"""Check that every finding is backed by text this run actually retrieved, before the Writer sees it.

A researcher returns findings as {claim, evidence_text, source_url, ...}. verify() accepts one only if:
  1. its source was retrieved in this run (a fetched page, a policy chunk, or a SQL result), and
  2. the evidence text appears in that source (exactly, or as a close fuzzy match), and
  3. every number in the claim also appears in the evidence.
Rule 3 is what stops "8-12%" in the claim from resting on a teaser paragraph that never said it.

Stdlib only, so it runs anywhere: `uv run evidence.py` runs the self-check.
"""

import json
import re
from difflib import SequenceMatcher

# Partial pages and snippets get accepted, but the Writer must label them.
ACCESS_LABELS = {"full", "partial", "snippet"}

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def normalize(text: str) -> str:
    """Lowercase, straighten quotes and dashes, drop thousands separators, collapse whitespace."""
    text = text.lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[‒–—−]", "-", text)
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)  # 17,321 -> 17321
    return re.sub(r"\s+", " ", text).strip()


def numbers(text: str) -> set[str]:
    """The numbers in a text, normalised: '$127.21 million' -> {'127.21'}, '8–12%' -> {'8', '12'}."""
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in _NUMBER.findall(normalize(text))}


def span_in(evidence: str, source: str, threshold: float = 0.9) -> bool:
    """True if evidence appears in source: exactly after normalising, or as a close fuzzy match
    (extraction differences: a dropped footnote marker, different whitespace in a table)."""
    ev, src = normalize(evidence), normalize(source)
    if not ev:
        return False
    if ev in src:
        return True
    # Fuzzy: find where the evidence's longest shared block sits, then compare against the window
    # of the source around it. Scoring the window, not the whole page, stops matches scattered
    # across a long page from adding up to a false pass.
    matcher = SequenceMatcher(None, ev, src, autojunk=False)
    block = matcher.find_longest_match(0, len(ev), 0, len(src))
    if block.size < min(20, len(ev)):
        return False
    start = max(0, block.b - block.a - 10)
    window = src[start : start + len(ev) + 20]
    # Share of the evidence's characters found in the window, in order
    matched = sum(b.size for b in SequenceMatcher(None, ev, window, autojunk=False).get_matching_blocks())
    return matched / len(ev) >= threshold


def _numbers_in_rows(rows: list) -> set[str]:
    """Every number in a SQL result, including rounded forms (0.4723 also matches 0.47 and 47.2%)."""
    found: set[str] = set()
    for row in rows:
        for value in row if isinstance(row, (list, tuple)) else [row]:
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                for x in (value, value * 100):  # shares are often reported as percentages
                    for digits in range(0, 4):
                        found |= numbers(f"{round(x, digits)}")
            else:
                found |= numbers(str(value))
    return found


def verify(finding: dict, pages: dict[str, dict], sql_results: dict[str, dict]) -> tuple[bool, str]:
    """Check one finding against what this run retrieved. Returns (ok, reason)."""
    claim = finding.get("claim", "")
    evidence = finding.get("evidence_text", "")
    url = finding.get("source_url", "")
    if not claim or not evidence or not url:
        return False, "missing claim, evidence_text or source_url"

    missing = numbers(claim) - numbers(evidence)
    if missing:
        return False, f"numbers in the claim that aren't in the evidence: {sorted(missing)}"

    if url.startswith("sql:"):
        result = sql_results.get(url.removeprefix("sql:"))
        if result is None:
            return False, f"no query {url} was run in this research"
        absent = numbers(evidence) - _numbers_in_rows(result.get("rows", []))
        if absent:
            return False, f"evidence numbers not in the {url} result: {sorted(absent)}"
        return True, "matches query result"

    page = pages.get(url)
    if page is None:
        return False, f"{url} was not retrieved in this research"
    if page.get("access") == "blocked":
        return False, f"{url} was blocked; nothing was read"
    if not span_in(evidence, page.get("text", "")):
        return False, "evidence_text not found in the retrieved text"
    return True, f"found in retrieved text ({page.get('access', 'full')} access)"


_CITATION = re.compile(r"\[(F\d+)\]")


def check_citations(markdown: str, valid_ids: set[str]) -> dict:
    """After the Writer: which [F#] ids don't exist, and which findings were never cited."""
    cited = set(_CITATION.findall(markdown))
    return {"unknown": sorted(cited - valid_ids), "uncited": sorted(valid_ids - cited)}


if __name__ == "__main__":
    page_text = (
        "Led by women's basketball, between 8% and 12% of all revenue-sharing dollars "
        "this year are expected to be directed toward women's sports, per Opendorse."
    )
    pages = {
        "https://example.com/a": {"text": page_text, "access": "full"},
        "https://example.com/teaser": {"text": "Subscribe to continue reading.", "access": "partial"},
        "https://example.com/b": {"text": "x", "access": "blocked"},
    }
    sql = {"q1": {"columns": ["share"], "rows": [[0.4723], [17321]]}}

    def check(**f):
        return verify(f, pages, sql)

    ok, _ = check(claim="Opendorse expects 8–12% of revenue-sharing dollars to go to women's sports",
                  evidence_text="between 8% and 12% of all revenue-sharing dollars this year are expected to be directed toward women's sports",
                  source_url="https://example.com/a")
    assert ok
    # A number the evidence doesn't contain
    ok, why = check(claim="Opendorse expects 15% to go to women's sports",
                    evidence_text="between 8% and 12% of all revenue-sharing dollars",
                    source_url="https://example.com/a")
    assert not ok and "15" in why
    # Evidence that isn't on the page (a paywalled teaser)
    ok, _ = check(claim="Women got 30%", evidence_text="women got 30%", source_url="https://example.com/teaser")
    assert not ok
    # A page that was never fetched, and a blocked one
    assert not check(claim="x", evidence_text="x", source_url="https://never.example")[0]
    assert not check(claim="x", evidence_text="x", source_url="https://example.com/b")[0]
    # Fuzzy: curly quotes and a dropped word still match
    ok, _ = check(claim="Basketball leads", evidence_text="Led by women’s basketball, between 8% and 12% of revenue-sharing dollars",
                  source_url="https://example.com/a")
    assert ok
    # SQL: rounded forms and thousands separators match the result
    assert check(claim="47.2% of athletes", evidence_text="share = 0.47 (47.2%)", source_url="sql:q1")[0]
    assert check(claim="17,321 deals", evidence_text="17321", source_url="sql:q1")[0]
    assert not check(claim="52%", evidence_text="0.52", source_url="sql:q1")[0]
    assert not check(claim="x", evidence_text="1", source_url="sql:q9")[0]
    # Citations
    report = "Women are 47% of athletes [F1]. Revenue sharing differs [F2][F7]."
    assert check_citations(report, {"F1", "F2", "F3"}) == {"unknown": ["F7"], "uncited": ["F3"]}
    print(json.dumps({"evidence self-check": "ok"}))
