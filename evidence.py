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
        # The other way: n has more digits than the result showed (results are rounded to 4 places,
        # and a researcher wrote 25.92389973836965 for 25.9239). Fine if it rounds to that value.
        p_places = len(p.split(".")[1]) if "." in p else 0
        if places > p_places and p_places >= 2 and abs(round(target, p_places) - float(p)) < 1e-9:
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


def resolve_source(finding: dict, pages: dict[str, dict]) -> None:
    """A researcher sometimes cites a policy document by its web address (the court PDF) instead of
    the 'policy:<chunk>' id search_policy gave it. Point the finding at the retrieved chunk of that
    document whose text holds the evidence, if there is one; verify() still checks everything."""
    url = finding.get("source_url", "")
    if url in pages or url.startswith(("sql:", "policy:")):
        return
    evidence = finding.get("evidence_text", "")
    parts = [p for p in re.split(r"\s*(?:\.\.\.|…)\s*", evidence) if len(p.strip()) >= 15] or [evidence]
    for key, page in pages.items():
        if page.get("document_url") == url and all(span_in(p, page.get("text", "")) for p in parts):
            finding["source_url"] = key
            return


def verify(finding: dict, pages: dict[str, dict], sql_results: dict[str, dict]) -> tuple[bool, str]:
    """Check one finding against what this run retrieved. Returns (ok, reason)."""
    claim = finding.get("claim", "")
    evidence = finding.get("evidence_text", "")
    url = finding.get("source_url", "")
    if not claim or not evidence or not url:
        return False, "missing claim, evidence_text or source_url"

    in_evidence = numbers(evidence)
    missing = {n for n in numbers(claim) if not _covered(n, in_evidence)}
    if missing and url.startswith("sql:") and url.removeprefix("sql:") in sql_results:
        # A year the query filtered on ("WHERE survey_year = 2025") is a fact about the result even
        # when it isn't a column: "342 D-I schools in 2024-25" from that query is right. Only years:
        # every other number must still be in the rows.
        query_years = {n for n in numbers(sql_results[url.removeprefix("sql:")].get("query", "")) if n.isdigit() and 2000 <= int(n) <= 2099}
        missing = {n for n in missing if n not in query_years}
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
    # A quote with an omission ("A ... B") is fine if every part is in the text: researchers join
    # two sentences of a court order that way, and the joined string is in no source.
    parts = [p for p in re.split(r"\s*(?:\.\.\.|…)\s*", evidence) if len(p.strip()) >= 15] or [evidence]
    if not all(span_in(p, page.get("text", "")) for p in parts):
        return False, "evidence_text not found in the retrieved text"
    return True, f"found in retrieved text ({page.get('access', 'full')} access)"


_CITATION = re.compile(r"\[(F\d+(?:\s*,\s*F\d+)*)\]")  # [F3], and the Writer's occasional [F3, F7]


def check_citations(markdown: str, valid_ids: set[str]) -> dict:
    """After the Writer: which [F#] ids don't exist, and which findings were never cited."""
    cited = {fid for group in _CITATION.findall(markdown) for fid in re.findall(r"F\d+", group)}
    return {"unknown": sorted(cited - valid_ids), "uncited": sorted(valid_ids - cited)}


_CHART_BLOCK = re.compile(r"```chart\s*\n(.*?)```", re.S)
CHART_TYPES = {"bar", "line"}
MAX_SERIES = 3  # the palette's first three slots stay distinguishable for colorblind readers


def check_chart(spec: dict, findings_by_id: dict[str, dict]) -> tuple[bool, str]:
    """A chart gets the same rule as a finding: every value it plots must appear in the evidence
    of the findings it cites. Labels aren't checked as figures (they're years and names)."""
    if spec.get("type") not in CHART_TYPES:
        return False, f"type must be one of {sorted(CHART_TYPES)}"
    labels, series, cited = spec.get("labels"), spec.get("series"), spec.get("sources")
    if not (isinstance(labels, list) and isinstance(series, list) and isinstance(cited, list) and labels and series):
        return False, "needs labels, series and sources lists"
    if len(series) > MAX_SERIES:
        return False, f"at most {MAX_SERIES} series"
    unknown = [c for c in cited if c not in findings_by_id]
    if unknown:
        return False, f"cites unknown findings {unknown}"
    pool: set[str] = set()
    for c in cited:
        pool |= numbers(findings_by_id[c].get("evidence_text", ""))
    for s in series:
        values = s.get("values") if isinstance(s, dict) else None
        if not isinstance(values, list) or len(values) != len(labels):
            return False, "each series needs one value per label"
        for v in values:
            if v is None:
                continue  # a gap in the series
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return False, f"value {v!r} isn't a number"
            if not all(_covered(n, pool) for n in numbers(repr(v))):
                return False, f"value {v} isn't in the cited findings' evidence"
    return True, "values match cited evidence"


def check_charts(markdown: str, findings: list[dict]) -> tuple[str, list[str]]:
    """Drop every ```chart block that fails check_chart. Returns (markdown, reasons for drops)."""
    by_id = {f["id"]: f for f in findings}
    dropped: list[str] = []

    def keep_or_drop(m: re.Match) -> str:
        try:
            spec = json.loads(m.group(1))
        except ValueError:
            dropped.append("chart isn't valid JSON")
            return ""
        ok, reason = check_chart(spec, by_id) if isinstance(spec, dict) else (False, "chart isn't an object")
        if not ok:
            dropped.append(reason)
            return ""
        # Re-serialise compactly: the page parses it, and it's what /chat clients see
        return "```chart\n" + json.dumps(spec, separators=(",", ":")) + "\n```"

    return _CHART_BLOCK.sub(keep_or_drop, markdown), dropped


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
    # Extra digits that round to the shown value are fine; a different value is not
    sql["q4"] = {"query": "SELECT 1", "columns": ["pct"], "rows": [[25.9239]]}
    assert check(claim="25.92%", evidence_text="pct=25.92389973836965", source_url="sql:q4")[0]
    assert not check(claim="25.95%", evidence_text="pct=25.9531", source_url="sql:q4")[0]
    # A year the query filtered on counts; any other number must be in the rows
    sql["q3"] = {"query": "SELECT count(*) FROM sports WHERE survey_year = 2025 AND sports = 'Soccer'", "columns": ["n"], "rows": [[342]]}
    assert check(claim="342 schools in 2024-25", evidence_text="school_count=342", source_url="sql:q3")[0]
    assert not check(claim="342 schools in 2022-23", evidence_text="school_count=342", source_url="sql:q3")[0]
    assert not check(claim="350 schools in 2024-25", evidence_text="school_count=342", source_url="sql:q3")[0]
    # A policy chunk cited by its document's web address resolves to the chunk that holds the evidence
    pages["policy:order#12"] = {"text": "Defendants agreed to pay $2.576 billion in total.", "access": "full", "document_url": "https://court.example/order.pdf"}
    f = {"claim": "$2.576 billion in total", "evidence_text": "Defendants agreed to pay $2.576 billion in total.", "source_url": "https://court.example/order.pdf"}
    resolve_source(f, pages)
    assert f["source_url"] == "policy:order#12" and verify(f, pages, sql)[0]
    # Ellipsis quotes: each part must be on the page; an invented part still fails
    long_page = {"text": "Defendants agreed to pay $2.576 billion in total. Other text here. The fund of $1.976 billion is labeled NIL.", "access": "full"}
    pages["https://example.com/order"] = long_page
    assert check(claim="$2.576 billion in total", evidence_text="Defendants agreed to pay $2.576 billion in total. ... The fund of $1.976 billion is labeled NIL.", source_url="https://example.com/order")[0]
    assert not check(claim="$2.576 billion", evidence_text="Defendants agreed to pay $2.576 billion in total. ... The fund was invented by the researcher here.", source_url="https://example.com/order")[0]
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
    assert check_citations("A [F1, F3].", {"F1", "F2", "F3"}) == {"unknown": [], "uncited": ["F2"]}
    # Charts: values must come from the cited findings' evidence (rounding allowed)
    found = [{"id": "F1", "evidence_text": "survey_year=2023, avg_revenue=3108725.8571, avg_expenses=6119890.93"},
             {"id": "F2", "evidence_text": "survey_year=2024, avg_revenue=4285628.0, avg_expenses=6776757.0"}]
    good = {"type": "bar", "title": "t", "unit": "$", "labels": ["2022-23", "2023-24"],
            "series": [{"name": "Revenue", "values": [3108726, 4285628]}], "sources": ["F1", "F2"]}
    assert check_chart(good, {f["id"]: f for f in found})[0]
    bad = {**good, "series": [{"name": "Revenue", "values": [3200000, 4285628]}]}
    assert "3200000" in check_chart(bad, {f["id"]: f for f in found})[1]
    md, why = check_charts("Text [F1].\n\n```chart\n" + json.dumps(bad) + "\n```\nMore.", found)
    assert "```chart" not in md and why
    md, why = check_charts("```chart\n" + json.dumps(good) + "\n```", found)
    assert "```chart" in md and not why
    print(json.dumps({"evidence self-check": "ok"}))
