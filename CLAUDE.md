# CLAUDE.md

Read `PROJECT_BRIEF.md` first: it is the design and the source of truth for what we're building.
This file is about how to work in the repo.

## Commands

```bash
uv sync                                   # install deps (commit uv.lock afterwards)
gcloud auth application-default login     # Vertex AI credentials for Gemini
uv run build_eada.py --inspect            # list columns of the raw EADA files
uv run build_eada.py                      # OFFLINE build: data/eada.duckdb + data/eada_mappings.json
uv run app.py                             # http://localhost:8000
uv run evidence.py                        # self-check (stdlib only)
uv run web.py --selfcheck                 # access-detection self-check, no network
uv run policy.py "revenue sharing cap"    # search the policy corpus
uv run evals/citation_eval.py             # citation eval against a running server
uv run runlog.py [last | tavily]          # run traces (findings, rejections + reasons, tokens), Tavily credits
git push                                  # deploys: Cloud Run builds main via Developer Connect
```

Deployment details are in README.md. The deployed app reads `eada.duckdb` from a Cloud Storage
bucket mounted at `/data` (`EADA_DB=/data/eada.duckdb`), so don't commit the database; do commit
`data/eada_mappings.json`.

## Conventions (follow the course templates)

- Same stack as `wikipedia-deep-research` and `subway-survey-text2sql-improved`: OpenAI Agents SDK
  + LiteLLM → Vertex AI Gemini, FastAPI, DuckDB, `uv`. Don't swap frameworks.
- Flat modules at the repo root, short and readable. Comments explain *why*, not what.
- Tools never raise to the model: catch exceptions and return `{"error": ...}` JSON it can reason about.
- The DuckDB connection is read-only with external access disabled. The security boundary is the
  connection, not the prompt.
- Keep `context_panel.py` and `context-panel.js` as in the template.
- Self-checks live in `if __name__ == "__main__":` blocks, as in the templates.
- `POST /chat` must keep the Project 1 shape: `{response, session_id, tool_calls}`; every tool call
  carries an `agent` field.

## Rules specific to this project

- **Never invent EADA column names.** Inspect the files (`--inspect`) and the data dictionary,
  then edit `FILE_TABLES` in `build_eada.py`, `sql/views.sql`, and `data/column_notes.json`.
  Placeholders in those files are marked `TODO`.
- **Never invent NIL figures.** Rows in `data/nil_facts.csv` need a URL and a method note. Seed
  rows are `verified_against_primary = false` until checked against the primary report.
- Findings reach the Writer only after `evidence.verify()` passes. Don't weaken this to make a
  demo work; fix the researcher prompt or the fetch instead.
- No paywall or bot-protection bypassing (archive sites, bypass services, spoofed clients).
- Keep the money flows (athletics spending / revenue sharing / third-party NIL, plus House
  settlement back-pay damages, which are none of these) separate in prompts and output.
- Label every number with the period it covers.

## Where things are

| File | Role |
|---|---|
| `app.py` | FastAPI app, `/run` SSE for the page, `/chat` JSON, clarification sessions |
| `research.py` | Lead, Researchers (data/news/policy), Writer, `deep_research()` |
| `eada.py` | read-only DuckDB + mappings for the Data Researcher |
| `web.py` | search, fetch, access detection, publication dates, cache, source tiers |
| `policy.py` | BM25 search over `data/policy/*.md` |
| `evidence.py` | evidence verification and citation checks |
| `runlog.py` | per-run traces in `data/cache/runs/`, Tavily usage |
| `build_eada.py`, `sql/views.sql` | offline build step |
| `data/` | `nil_facts.csv`, `column_notes.json`, `sources.json`, `policy/`, `raw/` (gitignored) |
| `evals/` | questions + citation eval |
