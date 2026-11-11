# CLAUDE.md

README.md describes the system as built (pipeline, evals, results); docs/DEPLOY.md the deployment. This file is about
how to work in the repo.

## Commands

```bash
uv sync                                    # install deps (commit uv.lock afterwards)
gcloud auth application-default login      # Vertex AI credentials for Gemini
uv run scripts/build_eada.py --inspect     # list columns of the raw EADA files
uv run scripts/build_eada.py               # OFFLINE build: data/eada.duckdb + data/eada_mappings.json
uv run app.py                              # http://localhost:8000
uv run evidence.py                         # evidence-check self-test (stdlib only)
uv run evals/scoring.py                    # scorer self-test
uv run web.py --selfcheck                  # access-detection self-check, no network
uv run policy.py "revenue sharing cap"     # search the policy corpus
uv run evals/run_evals.py plan             # the Lead alone: plans and reviews (cheap)
uv run evals/run_evals.py scope            # the scope extractor alone (cheap)
uv run evals/run_evals.py data             # the Data Researcher alone, gold answers from the database
uv run evals/run_evals.py e2e [--ids a,b]  # whole runs: gold numbers, required points, citations
uv run evals/compare_baseline.py           # README examples vs. Gemini + Google Search -> docs/examples.md
uv run runlog.py [last | tavily]           # run traces, per-agent scorecard, Tavily credits
git push                                   # deploys: Cloud Run builds main via Developer Connect
```

Deployment details are in docs/DEPLOY.md. The deployed app reads `eada.duckdb` from a Cloud Storage
bucket mounted at `/data` (`EADA_DB=/data/eada.duckdb`), so don't commit the database; do commit
`data/eada_mappings.json`. Re-upload the database after changing the build, `data/column_notes.json`
or `data/nil_facts.csv`.

## Conventions (follow the course templates)

- Same stack as `wikipedia-deep-research` and `subway-survey-text2sql-improved`: OpenAI Agents SDK
  + LiteLLM → Vertex AI Gemini, FastAPI, DuckDB, `uv`. Don't swap frameworks.
- Runtime modules flat at the repo root (the buildpack runs `app.py` from there), short and
  readable. The page is in `static/`, offline scripts in `scripts/`, docs in `docs/`. Comments
  explain *why*, not what.
- Tools never raise to the model: catch exceptions and return `{"error": ...}` JSON it can reason about.
- The DuckDB connection is read-only with external access disabled. The security boundary is the
  connection, not the prompt.
- Keep `context_panel.py` and `context-panel.js` as in the template (the page moves the panel into
  its Film room tab; the files are unchanged).
- Self-checks live in `if __name__ == "__main__":` blocks, as in the templates.
- `POST /chat` must keep the Project 1 shape: `{response, session_id, tool_calls}`; every tool call
  carries an `agent` field.
- Edit Python with the Edit tool, not shell heredocs: escapes like `\b` and `\n` have come through
  shell edits as control characters.

## Rules specific to this project

- **Assignment requirements** (README, "Assignment requirements"): three agent shapes; the Lead fans
  out to several researchers in one turn, in parallel, in fresh contexts; the Lead goes back for
  more when findings leave a gap, up to a turn cap (`MAX_ROUNDS`); vague questions get a clarifying
  question; accurate citations. Don't simplify any of these away.
- **Never invent EADA column names.** Inspect the files (`--inspect`) and the data dictionary,
  then edit `FILE_TABLES` in `scripts/build_eada.py`, `sql/views.sql`, and `data/column_notes.json`.
- **Never invent NIL figures.** Rows in `data/nil_facts.csv` need a URL and a method note. Rows are
  `verified_against_primary = false` until checked against the primary source.
- Findings reach the Lead's review and the Writer only after `evidence.verify()` passes, and for SQL
  findings `evidence.fits_task()` (the query filters on the task's extracted scope). Don't weaken
  either to make a demo work; fix the researcher prompt or the fetch instead. (Relaxations so far
  each accept text that is provably in the source: rounded or over-precise forms of a shown value,
  ellipsis quotes whose parts are all present, years and cutoffs a query filtered on, a finding
  citing two queries that both ran.)
- No lists of names in the checks: the scope extractor maps "SEC" to the data's spelling using
  `eada.scope_vocabulary()`, read from the database. `uv run evals/run_evals.py scope` tests it.
- Broad questions (the Lead's `question_type`) get more news budget and a longer answer; data
  questions must keep the path the e2e evals measure. The Writer never adds facts from its own
  knowledge, in either mode.
- A wrong answer gets an eval case before (or with) its fix, with gold values computed by SQL where
  possible, so it stays fixed.
- No paywall or bot-protection bypassing (archive sites, bypass services, spoofed clients).
- Keep the money flows (athletics spending / revenue sharing / third-party NIL, plus House
  settlement back-pay damages, which are none of these) separate in prompts and output.
- Label every number with the period it covers.

## Where things are

| File | Role |
|---|---|
| `app.py` | FastAPI app, `/run` SSE for the page, `/chat` JSON, clarifications and follow-up conversations |
| `research.py` | Lead (plan, review, question type), scope extractor, Data / Policy / News Researchers, Writer, repair turn, citations and source cards, `deep_research()` |
| `eada.py` | read-only DuckDB, the task-sized schema (`schema_for`), `find_columns` |
| `web.py` | search, fetch, access detection, publication dates, cache, source tiers |
| `policy.py` | BM25 search over `data/policy/*.md` |
| `evidence.py` | evidence verification, query-fit check against the task's scope, chart and citation checks |
| `runlog.py` | per-run traces in `data/cache/runs/`, per-agent scorecard, Tavily usage |
| `static/index.html`, `static/playbook.js` | the page: chalkboard play diagram (driven by `play` SSE events from `ResearchHooks`), Film room tab, whiteboard chat, citation cards |
| `scripts/build_eada.py`, `sql/` | offline build step (`ncaa_division` is derived here) |
| `data/` | `nil_facts.csv`, `column_notes.json`, `sources.json`, `policy/`; `raw/`, `cache/`, the database (gitignored) |
| `evals/` | `plan_cases`, `data_cases`, `e2e_cases` (gold SQL), `run_evals.py`, `compare_baseline.py`, `scoring.py` |
| `docs/` | `DEPLOY.md`; `examples.md` and `examples-data.md` (generated by `compare_baseline.py`) |
