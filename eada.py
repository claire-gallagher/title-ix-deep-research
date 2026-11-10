"""Read-only access to the EADA database for the Data Researcher.

build_eada.py builds data/eada.duckdb and data/eada_mappings.json once, offline. This module only
reads them. The connection opens lazily, so the app still starts (and says what's missing) before
the database has been built.
"""

import json
import re
import os
from decimal import Decimal
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
DB_PATH = Path(os.environ.get("EADA_DB", HERE / "data" / "eada.duckdb"))
MAPPINGS_PATH = HERE / "data" / "eada_mappings.json"
MAX_ROWS = 100

_db = None
_mappings = None


def _connect():
    global _db, _mappings
    if _db is None:
        if not DB_PATH.exists() or not MAPPINGS_PATH.exists():
            raise FileNotFoundError("No database. Run `uv run scripts/build_eada.py` first.")
        _mappings = json.loads(MAPPINGS_PATH.read_text())
        # The security boundary is the connection, not the prompt: read-only, no file or network
        # access, and the agent can't SET its way out of either.
        _db = duckdb.connect(
            str(DB_PATH),
            read_only=True,
            config={"enable_external_access": False, "lock_configuration": True},
        )
    return _db, _mappings


def read_mappings() -> dict:
    """Tables, their columns (name, type, description, is_enum), and the known gotchas."""
    _, mappings = _connect()
    return {
        "tables": [
            {
                "name": t["name"],
                "description": t["description"],
                "columns": [
                    {"name": c["name"], "type": c["type"], "description": c["description"], "is_enum": "enum_values" in c}
                    for c in t["columns"]
                ],
            }
            for t in mappings["tables"]
        ],
        "gotchas": mappings.get("gotchas", []),
    }


# --- A schema sized to the task ---
# The whole schema with every note is ~25k characters, resent on every turn of a Data Researcher.
# Each task gets: every table (name and purpose), the columns any query needs, the columns its words
# point at, and the notes about those columns. find_columns() reaches the rest.

KEY_COLUMNS = {"survey_year", "unitid", "institution_name", "ncaa_division", "sports", "state_cd",
               "basketball_conference", "football_conference", "power_conference", "conference", "alias"}
# Notes that prevent the commonest wrong answers: always included.
ALWAYS_NOTES = {"filtering by division", "school-level vs. team-level money", "team money is in sports"}
# Everyday words -> words that appear in column names and descriptions
SYNONYMS = {
    "spend": "expense", "spending": "expense", "spent": "expense", "cost": "expense", "budget": "expense",
    "deficit": "revenue expense", "profit": "revenue expense", "money": "revenue expense",
    "income": "revenue", "athlete": "partic undup", "athletes": "partic undup", "roster": "partic",
    "participation": "partic undup", "players": "partic", "coach": "coach salary", "coaches": "coach salary",
    "salary": "salary", "pay": "salary", "scholarship": "studentaid", "aid": "studentaid",
    "recruiting": "recruit", "proportionality": "gap share", "equity": "gap share",
    "enrollment": "enrollment undergrad", "students": "enrollment undergrad", "nil": "nil",
    "collective": "nil", "settlement": "house", "sharing": "revenue_sharing share",
}
STOP = {"what", "which", "with", "from", "that", "this", "have", "does", "their", "they", "over",
        "about", "year", "years", "school", "schools", "team", "teams", "women", "womens", "men",
        "mens", "division", "average", "total", "each", "data", "eada", "between", "compare", "the"}


def _terms(text: str) -> set[str]:
    """The task's own words (4+ letters, not stop words) plus their synonyms' column words."""
    words = {re.sub(r"'s$", "", w).strip("'") for w in re.findall(r"[a-z_']{4,}", text.lower())} - STOP
    terms = set(words)
    for w in words:
        terms |= set(SYNONYMS.get(w, "").split())
        if w.endswith("s"):
            terms |= {w[:-1]} | set(SYNONYMS.get(w[:-1], "").split())
    return {t for t in terms if len(t) >= 3}


def _matches(column: dict, terms: set[str]) -> bool:
    hay = (column["name"] + " " + (column.get("description") or "")).lower()
    return any(t in hay for t in terms)


def schema_for(task: str) -> str:
    """The schema and notes for one task, as compact text."""
    _, mappings = _connect()
    terms = _terms(task)
    lines, kept = [], set()
    for t in mappings["tables"]:
        table_hit = t["name"].replace("v_", "").replace("_", " ") in task.lower() or _matches(
            {"name": t["name"], "description": t["description"]}, terms)
        cols = [c for c in t["columns"] if c["name"] in KEY_COLUMNS or _matches(c, terms)
                or (table_hit and t["name"].startswith(("v_", "nil")))]
        lines.append(f"TABLE {t['name']}: {t['description']}")
        for c in cols:
            enum = " [enum: get_enum_values]" if "enum_values" in c else ""
            lines.append(f"  {c['name']} {c['type']}{enum}: {c.get('description') or ''}")
            kept.add(c["name"])
        if len(cols) < len(t["columns"]):
            lines.append(f"  (+{len(t['columns']) - len(cols)} more columns: find_columns)")
    notes = [g for g in mappings.get("gotchas", []) if g["topic"] in ALWAYS_NOTES or kept & set(g.get("columns", []))]
    lines.append("\nGOTCHAS (read these: they describe mistakes that give wrong answers)")
    lines += [f"- {g['topic']}: {g['note']}" for g in notes]
    return "\n".join(lines)


def find_columns(query: str) -> dict:
    """Columns in any table whose name or description matches the query's words."""
    _, mappings = _connect()
    terms = _terms(query) or {query.lower()}
    found = [{"table": t["name"], "column": c["name"], "type": c["type"], "description": c.get("description")}
             for t in mappings["tables"] for c in t["columns"] if _matches(c, terms)]
    notes = [f"{g['topic']}: {g['note']}" for g in mappings.get("gotchas", [])
             if {f["column"] for f in found} & set(g.get("columns", []))]
    return {"columns": found[:40], "truncated": len(found) > 40, "notes": notes}


def enum_values(table: str, column: str) -> dict:
    _, mappings = _connect()
    t = next((t for t in mappings["tables"] if t["name"] == table), None)
    if t is None:
        return {"error": f"No table '{table}'. The tables are listed in your instructions' schema."}
    c = next((c for c in t["columns"] if c["name"] == column), None)
    if c is None:
        return {"error": f"No column '{column}' in {table}."}
    if "enum_values" not in c:
        return {"error": f"'{column}' is not an enum column."}
    return {"table": table, "column": column, "enum_values": c["enum_values"]}


def run_sql(query: str) -> dict:
    """Run one query. Returns {columns, rows, truncated} or {error}."""
    db, _ = _connect()
    # Tools run in threads. Each call gets its own cursor: DuckDB connections aren't thread-safe.
    with db.cursor() as cursor:
        try:
            cursor.execute(query)
            rows = cursor.fetchmany(MAX_ROWS + 1)
        except duckdb.Error as e:
            return {"error": str(e)}
        columns = [c[0] for c in cursor.description or []]
    # DECIMAL comes back as Decimal: make it a float so evidence.py can match rounded forms
    # Floats to 4 decimals: 714903.332103321 costs tokens in every later turn and says nothing more.
    # evidence.verify() checks against these stored values, so what the model sees is what it's held to.
    clean = [[round(float(v), 4) if isinstance(v, (Decimal, float)) else v for v in r] for r in rows[:MAX_ROWS]]
    return {"columns": columns, "rows": clean, "truncated": len(rows) > MAX_ROWS}


if __name__ == "__main__":
    # Smoke test: uv run eada.py "SELECT count(*) FROM institutions"
    import sys

    print(json.dumps(read_mappings(), indent=2)[:2000])
    print(run_sql(sys.argv[1] if len(sys.argv) > 1 else "SELECT count(*) FROM nil_facts"))
