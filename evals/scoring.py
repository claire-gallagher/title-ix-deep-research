"""Score an answer against gold facts. Stdlib only; used by run_evals.py and compare_baseline.py.

A case (evals/*.jsonl) can say:
  facts        [{label, value | sql, unit: "$" | "%" | "count", tol}]: numbers the answer must state.
               A fact with "sql" gets its value from the database (evals/gold.py), so gold answers
               are computed, not typed in. tol: relative for $ and counts (default 1.5%, so
               "$2.48 million" matches 2,481,325), absolute points for % (default 0.6).
  must_mention [[alternatives...], ...]: each group needs one of its phrases (case-insensitive regex).
  must_not     [regex, ...]: claims the answer must not make. Checked per sentence, and a sentence
               that hedges ("whether", "can't determine", ...) doesn't count: "EADA can't show whether
               Michigan is in compliance" is right, "Michigan is in compliance" is not.
"""

import re

_NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(%|percent(?:age points?)?|pp\b|points?\b|billion\b|bn\b|million\b|m\b|thousand\b|k\b)?",
    re.I,
)
_SCALE = {"billion": 1e9, "bn": 1e9, "million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}
# Hedges and attributions: "whether Michigan is in compliance" and "Michigan maintains it is in
# compliance" don't assert compliance
_HEDGE = re.compile(r"\b(whether|if|cannot|can't|can not|not possible|doesn't|does not|don't|no source|isn't possible|unable|not enough"
                    r"|maintains?|says?|said|claims?|asserts?|states?|stated|according to|argues?|alleges?|alleged)\b", re.I)


def numbers_in(text: str) -> list[tuple[float, bool]]:
    """Every number in a text as (value, is_percent): '$2.48 million' -> (2480000.0, False),
    '47.2%' -> (47.2, True), '2,481,325' -> (2481325.0, False)."""
    found = []
    for raw, unit in _NUMBER.findall(text):
        value = float(raw.replace(",", ""))
        unit = (unit or "").lower()
        is_pct = unit.startswith(("%", "percent", "pp", "point"))
        value *= _SCALE.get(unit, 1)
        found.append((value, is_pct))
    return found


def fact_found(fact: dict, text: str) -> bool:
    value = float(fact["value"])
    if fact.get("unit") == "%":
        # Magnitudes: a gap of -3.4 points is as often written "3.4 points below"
        tol = fact.get("tol", 0.6)
        return any(abs(v - abs(value)) <= tol for v, _ in numbers_in(text))
    tol = fact.get("tol", 0.015)
    return any(abs(v - value) <= tol * abs(value) for v, pct in numbers_in(text) if not pct)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def score(case: dict, text: str) -> dict:
    """{facts: [label: bool], mentions_ok, violations: [sentence], ok} for one answer."""
    facts = {f["label"]: fact_found(f, text) for f in case.get("facts", []) if f.get("value") is not None}
    missing_mentions = [group for group in case.get("must_mention", [])
                        if not any(re.search(alt, text, re.I) for alt in group)]
    violations = [s.strip() for s in _sentences(text) for pattern in case.get("must_not", [])
                  if re.search(pattern, s, re.I) and not _HEDGE.search(s)]
    return {
        "facts": facts,
        "facts_found": sum(facts.values()),
        "facts_total": len(facts),
        "missing_mentions": missing_mentions,
        "violations": violations,
        "ok": all(facts.values()) and not missing_mentions and not violations,
    }


if __name__ == "__main__":
    assert numbers_in("$2.48 million in 2023-24") == [(2480000.0, False), (2023.0, False), (24.0, False)]
    assert numbers_in("47.2% of athletes") == [(47.2, True)]
    assert fact_found({"value": 2481325, "unit": "$"}, "about $2.48 million")
    assert fact_found({"value": 2481325, "unit": "$"}, "$2,481,324.64")
    assert not fact_found({"value": 2481325, "unit": "$"}, "$1.07 million")
    assert fact_found({"value": 2.576e9, "unit": "$"}, "$2.576 billion")
    assert fact_found({"value": -3.1, "unit": "%"}, "a gap of -3.4 percentage points") is True
    case = {"must_not": [r"Michigan\b[^.]{0,40}\b(is|appears to be)\s+(in compliance|compliant|non-?compliant)"]}
    assert score(case, "EADA can't show whether Michigan is in compliance.")["ok"]
    assert not score(case, "Michigan is in compliance with Title IX.")["ok"]
    assert score(case, "Officially, Michigan maintains that it is in compliance.")["ok"]
    print("scoring self-check: ok")
