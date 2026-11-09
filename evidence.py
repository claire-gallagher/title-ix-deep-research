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


_ACADEMIC_YEAR = re.compile(r"\b(\d{2})(\d{2})-(\d{2})(\d{2})?\b")


def _end_year(m: re.Match) -> str:
    """'2024-25' / '2024-2025' -> '2025' when the years are consecutive: an academic year is named
    by its end year in EADA (survey_year 2025 = 2024-25). Other ranges are left alone."""
    century, start, end_short, end_long = m.groups()
    end = int(end_long) if end_long else int(end_short)
    end_full = f"{end_short}{end_long}" if end_long else f"{century}{end_short}"
    if end_long and end_short != century:
        return m.group(0)
    return end_full if end == (int(start) + 1) % 100 else m.group(0)


# Numbers that are part of a name, not a figure: "Power 4", "Big 12", "Pac-12", "Group of 5".
_NAMES_WITH_NUMBERS = re.compile(r"\b(power|group of)[ -]?(4|5|four|five)\b|\b(big|pac)[ -]?(10|12)\b|\bg5\b|\bp[45]\b")


def numbers(text: str) -> set[str]:
    """The numbers in a text, normalised: '$127.21 million' -> {'127.21'}, '8–12%' -> {'8', '12'},
    'in 2024-25' -> {'2025'}, 'Power 4 schools' -> set()."""
    text = _NAMES_WITH_NUMBERS.sub(" ", normalize(text))
    text = _ACADEMIC_YEAR.sub(_end_year, text)
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in _NUMBER.findall(text)}


def _covered(n: str, pool: set[str]) -> bool:
    """n is in pool, or is a rounded form of a more precise number there (2183217.66 from
    2183217.6551724137, 47 from 47.2): researchers round what a query or report gave them."""
    if n in pool:
        return True
    places = len(n.split(".")[1]) if "." in n else 0
    target = float(n)
    for p in pool:
        if "." in p and len(p.split(".")[1]) > places and abs(round(float(p), places) - target) < 1e-9:
            return True
    return False


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
                found |= numbers(repr(value))  # the exact value, as a researcher may copy it
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

    in_evidence = numbers(evidence)
    missing = {n for n in numbers(claim) if not _covered(n, in_evidence)}
    if missing:
        return False, f"numbers in the claim that aren't in the evidence: {sorted(missing)}"

    if url.startswith("sql:"):
        result = sql_results.get(url.removeprefix("sql:"))
        if result is None:
            return False, f"no query {url} was run in this research"
        absent = {n for n in numbers(evidence) if not _covered(n, _numbers_in_rows(result.get("rows", [])))}
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
    # Rounding a precise query value is fine; a different number is not
    sql["q2"] = {"columns": ["avg"], "rows": [[2018, 2183217.6551724137]]}
    assert check(claim="average $2,183,217.66 in 2017-18", evidence_text="survey_year=2018, avg=2183217.6551724137", source_url="sql:q2")[0]
    assert check(claim="about $2.18 million on average", evidence_text="avg=2183217.66", source_url="sql:q2")[0] is False  # 2.18 isn't there
    assert not check(claim="average $2,183,300", evidence_text="survey_year=2018, avg=2183217.6551724137", source_url="sql:q2")[0]
    # Academic years: '2024-25' is EADA survey_year 2025; a real range stays a range
    assert numbers("in 2024-25") == {"2025"} and numbers("2024-2025") == {"2025"}
    assert numbers("from 2016-2025") == {"2016", "2025"}
    # Numbers inside names aren't figures
    assert numbers("Power 4 schools in the Big 12 and Pac-12 spent 26%") == {"26"}
    # Citations
    report ="Women are 47% of athletes [F1]. Revenue sharing differs [F2][F7]."
    assert check_citations(report, {"F1", "F2", "F3"}) == {"unknown": ["F7"], "uncited": ["F3"]}
    print(json.dumps({"evidence self-check": "ok"}))
