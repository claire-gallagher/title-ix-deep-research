"""Read-only access to the EADA database for the Data Researcher.

build_eada.py builds data/eada.duckdb and data/eada_mappings.json once, offline. This module only
reads them. The connection opens lazily, so the app still starts (and says what's missing) before
the database has been built.
"""

import json
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
            raise FileNotFoundError("No database. Run `uv run build_eada.py` first.")
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


def enum_values(table: str, column: str) -> dict:
    _, mappings = _connect()
    t = next((t for t in mappings["tables"] if t["name"] == table), None)
    if t is None:
        return {"error": f"No table '{table}'. Call read_mappings for the tables."}
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
    clean = [[float(v) if isinstance(v, Decimal) else v for v in r] for r in rows[:MAX_ROWS]]
    return {"columns": columns, "rows": clean, "truncated": len(rows) > MAX_ROWS}


if __name__ == "__main__":
    # Smoke test: uv run eada.py "SELECT count(*) FROM institutions"
    import sys

    print(json.dumps(read_mappings(), indent=2)[:2000])
    print(run_sql(sys.argv[1] if len(sys.argv) > 1 else "SELECT count(*) FROM nil_facts"))
