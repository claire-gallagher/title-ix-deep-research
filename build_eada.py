"""Build the database and its mappings. Run once, offline, before app.py.

1. Load the raw EADA files (data/raw/eada/<year>/*.csv|xlsx) into data/eada.duckdb, one table per
   kind of file, every year stacked with a survey_year column and normalised column names.
2. Build institution_aliases, so "UC Berkeley" can find "University of California-Berkeley".
3. Run sql/views.sql: derived metrics like the proportionality gap.
4. Load data/nil_facts.csv as the nil_facts table.
5. Describe every column (type, enum values or samples), ask the model for a one-sentence
   description, merge in the hand-written notes from data/column_notes.json, and write it all to
   data/eada_mappings.json: the schema and gotchas placed in the Data Researcher's instructions.

  uv run build_eada.py --inspect    print each raw file's columns (do this first)
  uv run build_eada.py              build
  uv run build_eada.py --no-llm     build without model-written descriptions
"""

import json
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"
RAW = DATA / "raw" / "eada"  # data/raw/eada/2023/<files>, data/raw/eada/2024/<files>, ...
DB_PATH = DATA / "eada.duckdb"
MAPPINGS_PATH = DATA / "eada_mappings.json"
MAX_ENUM_VALUES = 60

# Which raw files make which table. Patterns match file names, case-insensitive.
# Checked with --inspect on 2019-20..2024-25 (download_eada.py). Despite its name, Schools.xlsx is
# the SPORT-level file (one row per school, year and sport: sportscode, sports); InstLevel.xlsx has
# one row per school per year.
FILE_TABLES = {
    "raw_institutions": ["instlevel*"],  # trimmed into `institutions` by sql/tables.sql
    "raw_sports": ["schools*"],  # trimmed into `sports`
}

# TODO after --inspect: the raw names (normalised: lowercase, underscores) of the columns every
# table needs under a standard name. The first one found wins.
STANDARD_NAMES = {
    "unitid": ["unitid", "unit_id"],
    "institution_name": ["institution_name", "instnm", "inst_name"],
    "classification_name": ["classification_name", "classificationname"],  # 2018-19 file drops the underscore
}

TABLE_DESCRIPTIONS = {
    "institutions": "EADA, one row per school per year: enrollment, unduplicated athlete counts, athletic aid, recruiting, coach salaries, unallocated and grand-total revenue/expenses. No per-team money: that's in sports.",
    "sports": "EADA, one row per school, year and SPONSORED sport: participants, revenue, expenses, game-day operating expenses and coaches by team gender. Join institutions on (unitid, survey_year) for division, state or enrollment.",
    "conferences": "Each school's football and basketball conference per year, from IPEDS (not EADA), with a power_conference flag (Power 5 through 2023-24, Power 4 from 2024-25; Notre Dame counts). Join on (unitid, survey_year): conferences change between years.",
    "institution_aliases": "Alternate spellings of institution names -> unitid. Resolve user wording here before filtering.",
    "nil_facts": "Curated NIL and revenue-sharing figures from reports (Opendorse, College Sports Commission), with source, period, method note and URL for every row. Not EADA data.",
    "v_proportionality": "Derived: female share of undergrads vs. female share of athletes (unduplicated) and the gap in percentage points, per school and year.",
    "v_spending_by_gender": "Derived: men's vs. women's team expenses, total and per athlete, per school and year.",
}


def normalize_column(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(name).strip().lower()).strip("_")


def read_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, low_memory=False)
    return pd.read_excel(path)  # .xlsx / .xls


def raw_files() -> list[tuple[str, Path]]:
    """(year, path) for every raw file, years taken from the folder names."""
    if not RAW.exists():
        raise SystemExit(f"No raw data. Download the EADA data files into {RAW}/<year>/ (see data/README.md).")
    return [
        (year_dir.name, path)
        for year_dir in sorted(RAW.iterdir()) if year_dir.is_dir()
        for path in sorted(year_dir.iterdir()) if path.suffix.lower() in (".csv", ".xlsx", ".xls")
    ]


def inspect() -> None:
    for year, path in raw_files():
        df = read_file(path)
        print(f"\n== {year}/{path.name}: {len(df):,} rows, {len(df.columns)} columns")
        print(", ".join(normalize_column(c) for c in df.columns))


def codebook_labels() -> dict[str, dict[str, str]]:
    """{table: {column: label}} from data/eada_codebook.csv, the variable tables of the EADA
    codebooks (InstlevelDataDoc / SchoolsDoc .doc in each year's zip), extracted cell by cell
    with Word. Labels only differ cosmetically between years, so the latest year's wins."""
    path = DATA / "eada_codebook.csv"
    if not path.exists():
        print(f"warning: {path.name} missing, columns get no codebook labels")
        return {}
    labels: dict[str, dict[str, str]] = {}
    for row in pd.read_csv(path).sort_values("survey_year").itertuples():
        labels.setdefault(row.file, {})[normalize_column(row.variable)] = f"Codebook label: {row.label}"
    return labels


PARTIC_COLUMNS =["partic_men", "partic_women", "partic_coed_men", "partic_coed_women"]


def drop_unsponsored(df: pd.DataFrame, year: str) -> pd.DataFrame:
    """Drop sport rows with no participants in any column.

    Decision: the 2019-20 file lists sports schools don't sponsor (44,556 all-zero rows of 62,168);
    every other year lists sponsored sports only. Dropping them at build time makes "a row = a team"
    true in every year, so team counts don't jump in 2019-20. A no-op in the other years."""
    has_participants = df[PARTIC_COLUMNS].fillna(0).sum(axis=1) > 0
    dropped = int((~has_participants).sum())
    if dropped:
        print(f"  {year}: dropped {dropped:,} sport rows with no participants (unsponsored)")
    return df[has_participants]


def load_tables(db: duckdb.DuckDBPyConnection) -> list[str]:
    tables = []
    files = raw_files()
    for table, patterns in FILE_TABLES.items():
        frames = []
        for year, path in files:
            if any(fnmatch(path.name.lower(), p) for p in patterns):
                df = read_file(path)
                df.columns = [normalize_column(c) for c in df.columns]
                for standard, candidates in STANDARD_NAMES.items():
                    found = next((c for c in candidates if c in df.columns), None)
                    if found and found != standard:
                        df = df.rename(columns={found: standard})
                df = df.copy()  # read_excel leaves wide frames fragmented; insert() warns per file otherwise
                df.insert(0, "survey_year", int(year) if year.isdigit() else year)
                if table == "raw_sports":
                    df = drop_unsponsored(df, year)
                frames.append(df)
        if not frames:
            print(f"warning: no raw files matched {table} {patterns}")
            continue
        # Columns that change between years stay as separate columns, NULL in the other years.
        # Renamed columns are mapped onto one name in STANDARD_NAMES (2015-16..2024-25: only
        # classification_name); recheck with --inspect when adding years.
        combined = pd.concat(frames, ignore_index=True, sort=False)
        db.register("frame", combined)
        # TEMP: the raw tables only feed sql/tables.sql; in the file they'd leave dead space behind.
        db.execute(f"CREATE TEMP TABLE {table} AS SELECT * FROM frame")
        db.unregister("frame")
        tables.append(table)
        print(f"{table}: {len(combined):,} rows from {len(frames)} files")
    return tables


def build_aliases(db: duckdb.DuckDBPyConnection) -> None:
    """Simple generated aliases; add hand-written ones (e.g. 'UCLA') in data/aliases_extra.csv."""
    names = db.execute("SELECT DISTINCT unitid, institution_name FROM institutions").fetchall()
    rows = set()
    for unitid, name in names:
        name = str(name)
        variants = {name, name.replace("-", " "), name.replace("-", ", "), re.sub(r"\s+", " ", name.replace("The ", ""))}
        for v in variants:
            rows.add((v.lower().strip(), unitid, name))
    extra = DATA / "aliases_extra.csv"
    if extra.exists():
        for alias, unitid in pd.read_csv(extra)[["alias", "unitid"]].itertuples(index=False):
            official = next((n for u, n in names if u == unitid), None)
            rows.add((str(alias).lower().strip(), unitid, official))
    db.execute("CREATE TABLE institution_aliases (alias VARCHAR, unitid BIGINT, institution_name VARCHAR)")
    db.executemany("INSERT INTO institution_aliases VALUES (?, ?, ?)", sorted(rows, key=str))


def describe(db: duckdb.DuckDBPyConnection, table: str) -> list[dict]:
    columns = []
    for name, dtype, *_ in db.execute(f"DESCRIBE {table}").fetchall():
        column = {"name": name, "type": dtype}
        q = f'"{name}"'  # quoted: normalised names can start with a digit
        if dtype in ("VARCHAR",):
            distinct = f"SELECT DISTINCT {q} AS v FROM {table} WHERE {q} IS NOT NULL"
            count = db.execute(f"SELECT count(*) FROM ({distinct})").fetchone()[0]
            if count <= MAX_ENUM_VALUES:
                column["enum_values"] = [r[0] for r in db.execute(f"{distinct} ORDER BY v").fetchall()]
            else:
                column["samples"] = [r[0] for r in db.execute(f"{distinct} LIMIT 5").fetchall()]
        else:
            column["samples"] = [r[0] for r in db.execute(f"SELECT {q} FROM {table} WHERE {q} IS NOT NULL LIMIT 3").fetchall()]
        columns.append(column)
    return columns


def model_descriptions(table: str, columns: list[dict]) -> dict:
    import litellm

    prompt = (
        f"These are the columns of the table `{table}`: {TABLE_DESCRIPTIONS.get(table, '')} "
        "Data from the U.S. Equity in Athletics Disclosure Act (EADA) unless stated otherwise. "
        "For each column, write one concise sentence on what it represents, using its name, type and "
        "values or samples. If you can't tell, say 'Unclear: check the data dictionary.' Return a JSON "
        "object mapping column name to description.\n\n" + json.dumps(columns, default=str)
    )
    response = litellm.completion(
        model="vertex_ai/gemini-3.5-flash-lite",
        vertex_location="global",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


IPEDS_RAW = DATA / "raw" / "ipeds"
# IPEDS conference codes, the same in every IC file 2015-2024 (checked against the dictionaries).
POWER_CODES = {102: "Atlantic Coast Conference", 107: "Big Ten Conference", 108: "Big Twelve Conference",
               130: "Southeastern Conference"}
PAC12 = 127  # a power conference through 2023-24; down to 2 members in 2024-25


def _ipeds_labels(dict_zip: Path) -> dict[str, dict[int, str]]:
    """{'confno1': {code: conference name}, 'confno2': {...}} from an IPEDS dictionary's Frequencies sheet."""
    import io
    import zipfile

    import openpyxl

    with zipfile.ZipFile(dict_zip) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith((".xlsx", ".xls")))
        wb = openpyxl.load_workbook(io.BytesIO(zf.read(name)), read_only=True)
    sheet = next(s for s in wb.sheetnames if s.lower().startswith("frequencies"))
    labels: dict[str, dict[int, str]] = {}
    for row in wb[sheet].iter_rows(values_only=True):
        var = str(row[1]).strip().lower() if row and row[1] else ""
        if var in ("confno1", "confno2"):
            labels.setdefault(var, {})[int(row[2])] = str(row[3]).strip()
    return labels


def load_conferences(db: duckdb.DuckDBPyConnection) -> bool:
    """`conferences`: each EADA school's football and basketball conference per year, from IPEDS.

    IPEDS Institutional Characteristics asks every school its conference per sport (CONFNO1
    football, CONFNO2 basketball). IC<Y> covers academic year Y-(Y+1) = EADA survey_year Y+1. The
    revised release (_rv) is used when the zip has one. Checked against realignment: 2023-24 BYU,
    UCF, Cincinnati, Houston to the Big 12; 2024-25 Texas, Oklahoma to the SEC, the Pac-12 split.
    """
    import io
    import zipfile

    frames = []
    for path in sorted(IPEDS_RAW.glob("IC20[0-9][0-9].zip")) if IPEDS_RAW.exists() else []:
        ic_year = int(path.stem[2:])
        dict_zip = IPEDS_RAW / f"IC{ic_year}_Dict.zip"
        labels = _ipeds_labels(dict_zip) if dict_zip.exists() else {}
        with zipfile.ZipFile(path) as zf:
            csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            name = next((n for n in csvs if "_rv" in n.lower()), csvs[0])
            df = pd.read_csv(io.BytesIO(zf.read(name)), encoding="utf-8-sig", low_memory=False)
        df.columns = [c.strip().lower() for c in df.columns]
        df = df[["unitid", "confno1", "confno2"]].copy()
        survey_year = ic_year + 1
        power = set(POWER_CODES) | ({PAC12} if survey_year <= 2024 else set())
        for code_col, out_col in (("confno1", "football_conference"), ("confno2", "basketball_conference")):
            # -2 'Not applicable' and other non-conference codes get no name
            df[out_col] = df[code_col].map(lambda c, m=labels.get(code_col, {}): m.get(int(c)) if pd.notna(c) and int(c) > 0 else None)
        # Power = either sport's conference is a power one, so Notre Dame (football independent,
        # ACC otherwise) counts, as in the House settlement ("Conference Defendants plus Notre Dame").
        df["power_conference"] = df.confno1.isin(power) | df.confno2.isin(power)
        df.insert(0, "survey_year", survey_year)
        df["ipeds_file"] = name
        frames.append(df.drop(columns=["confno1", "confno2"]))
    if not frames:
        print(f"warning: no IPEDS files in {IPEDS_RAW}: no conferences table (uv run download_eada.py --ipeds)")
        return False
    db.register("frame", pd.concat(frames, ignore_index=True))
    # Only EADA schools, so the table stays small and every row joins.
    db.execute("""CREATE TABLE conferences AS
        SELECT f.* FROM frame f JOIN institutions i USING (survey_year, unitid)""")
    db.unregister("frame")
    n, missing = db.execute("""SELECT (SELECT count(*) FROM conferences),
        (SELECT count(*) FROM institutions i ANTI JOIN conferences c USING (survey_year, unitid))""").fetchone()
    print(f"conferences: {n:,} rows from {len(frames)} IPEDS files ({missing} EADA school-years have no IPEDS row)")
    return True


def main() -> None:
    if "--inspect" in sys.argv:
        inspect()
        return

    DB_PATH.unlink(missing_ok=True)
    db = duckdb.connect(str(DB_PATH))
    load_tables(db)
    # Keep only the columns the agents need (see the header of sql/tables.sql for what's left out).
    db.execute((HERE / "sql" / "tables.sql").read_text())
    tables = ["institutions", "sports"]
    if load_conferences(db):
        tables.append("conferences")
    if "institutions" in tables:
        build_aliases(db)
        tables.append("institution_aliases")

    views_sql = (HERE / "sql" / "views.sql").read_text()
    if re.search(r"<[A-Z_]+>", views_sql):
        print("skipping sql/views.sql: it still has <PLACEHOLDER> column names")
    else:
        db.execute(views_sql)
        tables += [v[0] for v in db.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()]

    db.execute(f"CREATE TABLE nil_facts AS SELECT * FROM read_csv('{DATA / 'nil_facts.csv'}', header = true)")
    tables.append("nil_facts")

    notes = json.loads((DATA / "column_notes.json").read_text())
    codebook = codebook_labels()
    mappings = {"tables": [], "gotchas": notes.get("gotchas", [])}
    for table in tables:
        columns = describe(db, table)
        labels = codebook.get(table, {})
        # The model only describes columns with neither a note nor a codebook label (none, as of
        # 2015-16..2024-25): guessing from names is how you get "per Team" totals that silently
        # exclude football. A new column shows up here first; add a note for it instead.
        missing = [c for c in columns if c["name"] not in notes.get("columns", {}) and c["name"] not in labels]
        if missing:
            print(f"  {table}: no note or codebook label for {[c['name'] for c in missing]}")
        descriptions = model_descriptions(table, missing) if missing and "--no-llm" not in sys.argv else {}
        for column in columns:
            # Hand-written notes (checked against the data) > official codebook label > model's guess
            column["description"] = (
                notes.get("columns", {}).get(column["name"])
                or labels.get(column["name"])
                or descriptions.get(column["name"], "")
            )
            column.pop("samples", None)
        mappings["tables"].append({"name": table, "description": TABLE_DESCRIPTIONS.get(table, ""), "columns": columns})
    db.close()

    MAPPINGS_PATH.write_text(json.dumps(mappings, indent=2, default=str))
    print(f"Built {DB_PATH.name} and {MAPPINGS_PATH.name}: {', '.join(tables)}")


if __name__ == "__main__":
    main()
