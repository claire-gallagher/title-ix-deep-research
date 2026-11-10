# Project Brief: Title IX & NIL Deep Research Agent

This is the design document for Project 2 (Data Exploration Agent). It records every decision made
during planning so development can continue in Claude Code. `CLAUDE.md` tells Claude Code how to
work in this repo; this file says *what* we're building and *why*.

> **Since this brief (2026-11-09):** the system as built is described in the README. The main
> changes: the Lead works in structured calls (a plan, then a review that sends at most one round of
> follow-up tasks) instead of a tool loop; the Data Researcher sees only the part of the schema its
> task needs; rejected findings get one repair turn; citations are numbered per source with hover
> cards; evals test the Lead, the Data Researcher and whole runs separately, against gold answers
> computed from the database. File paths below predate the move to `static/`, `scripts/` and `docs/`.

---

## 1. One-paragraph summary

A deep research agent that answers questions about **gender equity in college athletics**:
participation, spending, and revenue at individual schools and conferences (from the federal
Equity in Athletics Disclosure Act data), and how **revenue sharing and NIL** are changing that
picture (from a curated table of NIL figures, primary policy documents, and live news). A Lead
plans the research and fans out to specialised Researchers that run in parallel; a Writer turns
their *verified* findings into a cited, dated report that separates what the official data shows,
what industry reports estimate, what the news says, and what nobody can answer yet.

**Path:** Deep Research (the recommended path), with a Text2SQL agent inside it as the Data
Researcher. We keep the Text2SQL quality bar (offline build step, mappings, trap-aware prompts)
because it makes the numbers trustworthy, and it lets us optionally show the
"plain run_sql vs. ours" comparison as a bonus.

**Sign-up sheet entry (draft):**
> **Dataset:** U.S. Dept. of Education Equity in Athletics (EADA) data files, multi-year, plus a
> curated NIL/revenue-sharing figures table (Opendorse reports, College Sports Commission NIL Go
> reports) and live news. **Question domain:** gender equity in college athletics participation
> and spending, and how revenue sharing and NIL are changing it. **Path:** Deep Research.

---

## 2. Target users

| User | What they ask |
|---|---|
| Student journalists / sports reporters | "How does School X's spending on women's teams compare to its peers?" |
| Athletes and recruits (and parents) | "Which Big Ten schools spend the most per athlete on women's soccer?" |
| Title IX coordinators / athletic department staff | "Where does our proportionality gap stand vs. our conference?" |
| Advocates and researchers | "What share of revenue-sharing money is going to women's sports?" |

The front end must state what the agent explores, who it's for, and how to use it (assignment
requirement).

---

## 3. Assignment requirements → how we meet them

| Requirement | Our design |
|---|---|
| Data the model doesn't know deeply | Per-school, per-year EADA figures; curated NIL figures from 2025–26 reports |
| Data via tools | `run_sql` (DuckDB over EADA + `nil_facts`), `search_web`/`fetch_page`, `search_policy`/`read_policy` |
| Combine evidence into a supported claim | Writer combines verified findings across researchers; every sentence cites a finding |
| State if data can't answer | Explicit "What the data can't tell us" section; known unanswerables listed in §8 |
| Show its work; `/chat` shape from Project 1 | `POST /chat` → `{response, session_id, tool_calls}`; each tool call has an `agent` field, including sub-agents |
| Front end: what/who/how | `index.html` intro section + example questions + live activity + context panel |
| Deployed to Cloud Run | `Dockerfile` + `deploy.sh`; DuckDB file baked into the image |
| ≥3 agent shapes with different jobs | Lead, three Researcher kinds (Data, News, Policy), Writer |
| Lead fans out in one turn, parallel, fresh contexts | `research(kind, task)` called several times in one Lead turn; each Researcher is a fresh `Runner.run` |
| Lead goes back for more, up to a turn cap | Prompt allows ≤2 follow-up rounds; code caps at `MAX_RESEARCHERS = 6` |
| Vague input requires clarification | Lead replies `CLARIFY: <question>`; app returns it and resumes with the user's answer |
| Accurately cited output | Structured findings with evidence spans, verified in code before the Writer sees them; citation-id check after |

---

## 4. Data sources

### 4.1 EADA (primary, structured) — the backbone

- **What:** Annual athletics data every co-ed, Title IV–participating institution with
  intercollegiate athletics must submit under the Equity in Athletics Disclosure Act.
  Participation by sport and gender, coaching staff and salaries, operating expenses, recruiting
  expenses, athletic student aid, revenues and expenses by team, undergraduate enrollment by gender.
- **Where:** https://ope.ed.gov/athletics/#/datafile/list (bulk data files by year).
- **First task:** confirm the bulk downloads still work and which years are available. Record the
  exact file names, formats and the data dictionary in `data/README.md`.
- **Key ID:** `unitid` (IPEDS ID). Allows a later join to IPEDS/College Scorecard if wanted.
- **Lag:** EADA reporting trails by a year or two, so it mostly *predates* revenue sharing
  (which began in the 2025–26 academic year). The Writer must always say which year a number is from.

**Known traps** (the reasons the Data Researcher needs offline context; verify each against the
data dictionary, then record it in `data/column_notes.json`):

1. **Duplicated vs. unduplicated participants.** One athlete on three teams counts three times in
   sport-level participation. Institution-level totals for proportionality should use the
   unduplicated count, if the file provides one.
2. **Co-ed teams** reported separately from men's and women's teams.
3. **Proportionality gap is derived, not a column:** female share of undergrads minus female
   share of athletes (in percentage points). Precompute it as a view.
4. **Institution name matching** ("UC Berkeley" vs "University of California-Berkeley"): build an
   alias table offline.
5. **Schema drift across years:** column names may change between annual files. Normalise in
   the build step and document it.
6. **Revenue vs. expense columns** and "total" vs. "per participant" figures are easy to confuse.
7. **Classification / conference / division** values must be matched exactly (enum values).
8. **Zero vs. missing** (a school without the sport vs. not reported).

### 4.2 Curated NIL / revenue-sharing table (`data/nil_facts.csv`) — built offline

NIL data is hard to trace: there is no open dataset of deals. What exists is aggregate figures in
reports. We hand-extract them into a small table with full provenance so the agent can query them
with SQL next to EADA, and cite them accurately.

Columns: `fact_id, source_org, report, source_type, retrieved_via, published, period_covered,
metric, sport, gender, value_low, value_high, unit, method_note, url, verified_against_primary`.

Sources to extract from:
- **Opendorse** annual reports ("NIL at Three", "NIL at Four", the 2026 edition if out) and topical
  reports (e.g. the March 2026 college basketball report PDF). Opendorse is an NIL marketplace;
  its data is platform data (deals on its marketplace plus athlete-disclosed earnings), so every
  row carries a method note. There's no public API; don't scrape athlete profiles.
- **College Sports Commission (CSC) NIL Go deal reports.** The official clearinghouse for
  third-party deals over $600, set up after the House v. NCAA settlement. Periodic reports with
  deal counts and values; limited sport breakdowns. Note: the first report (Sept 2025) overstated
  cleared deal value ($79.8M reported vs. $35.42M corrected), which is a useful example of why
  we record corrections.
- **On3 NIL valuations:** *estimates*, not payments. Store only with `source_type = estimate`.

The seed rows currently in `data/nil_facts.csv` came from secondary coverage found while planning.
They are marked `verified_against_primary = false`. **Replace or confirm each one against the
primary report before relying on it.**

### 4.3 Policy documents (`data/policy/`) — fixed corpus

Primary documents, saved as Markdown with a `source_url:` header line, chunked and searched locally:
- Title IX statute and athletics regulations (34 CFR 106.41), the 1979 Policy Interpretation
  (the three-part test), relevant OCR guidance, and any rescissions of prior guidance
- House v. NCAA settlement terms (revenue-sharing cap, CSC, NIL Go)
- NCAA / CSC rules on third-party NIL
- Any federal or state legislation on college athlete compensation that becomes relevant

Why a fixed corpus: clean, verifiable citations; keeps policy claims out of the noisier news layer.

### 4.4 Live news (web search) — context only

For anything after the last curated extraction: school-specific revenue-sharing splits, lawsuits,
OCR investigations, conference decisions. Search API: **Tavily** by default (returns extracted
content; free tier). Alternatives: Brave Search API, Exa. Keep `web.search()` pluggable.

---

## 5. Source tiers and the three money flows

**Every finding carries a `source_type`, and the Writer weighs them in this order:**

1. `official_filing` — EADA
2. `official_body` — CSC reports, Dept. of Education, NCAA
3. `policy_document` — statutes, regulations, settlement terms
4. `industry_report` — Opendorse etc. Always disclose method and the platform's commercial interest
5. `news`
6. `estimate` — On3 valuations, projections, forecasts. Always labelled as estimates

**Never compare different kinds of money without saying so.** Keep three flows separate:

| Flow | Who pays | Where it shows up |
|---|---|---|
| Athletics spending | School → its teams (expenses, scholarships, coaches) | EADA |
| Revenue sharing | School → athletes directly, capped at $20.5M per school per year (2025–26) | News, school statements, CSC; not in EADA yet |
| Third-party NIL | Brands / collectives → athletes | Opendorse reports, CSC NIL Go reports; never in EADA |

---

## 6. Architecture

```
          user question
                │
              Lead ── vague? ──► "CLARIFY: ..." returned to user; resumes with their answer
                │
   research(kind, task) × 2–4, in ONE turn  (parallel, fresh contexts)
     ┌──────────┼───────────────┐
 Data Researcher  News Researcher  Policy Researcher
  run_sql over     search_web,      search_policy,
  EADA + nil_facts fetch_page       read_policy
     └──────────┼───────────────┘
        structured findings (claim + evidence span + source + date + access)
                │
       evidence.verify()  ← in code: span must be in what was actually retrieved;
                │            numbers in the claim must appear in the evidence
      Lead reads verified findings; gaps? → research again (≤2 more rounds, ≤6 researchers total)
                │
              Writer  (sees only the question + verified findings, cites [F#])
                │
       evidence.check_citations()  ← unknown [F#] ids are flagged
                │
          report (markdown) + tool_calls (each with an `agent` field)
```

### Agents

- **Lead** (`gemini-3.5-flash`): asks for clarification when the question is vague (no school /
  conference / year / metric where one is needed). Otherwise splits into 2–4 tasks, each assigned
  to a researcher *kind*, all in the same turn. Reads findings, re-dispatches for gaps, replies DONE.
- **Data Researcher** (`gemini-3.5-flash-lite`, Text2SQL): `read_mappings`, `get_enum_values`,
  `run_sql`. Read-only DuckDB connection. Returns findings whose `source_url` is `sql:q<N>` and
  whose evidence is the value(s) from that query result.
- **News Researcher**: `search_web`, `fetch_page`. Prefers primary sources: when an article cites
  a report, filing or press release, fetch that and cite it instead. Must return publication date
  (from page metadata, not the site header) and `access`.
- **Policy Researcher**: `search_policy`, `read_policy` over `data/policy/`.
- **Writer** (`gemini-3.5-flash`): one call after research. Sees only the question and verified
  findings (`F1..Fn`). Structure: short answer → sections → data table where numbers are compared
  → "What the data can't tell us" → Sources. Every factual sentence ends with `[F#]`. Labels every
  number with its period. Never mixes the three money flows without saying so.

### Finding schema (researchers' structured output)

```json
{
  "claim": "Opendorse projected 8–12% of revenue-sharing dollars would go to women's sports",
  "evidence_text": "<exact sentence(s) copied from the retrieved page or query result>",
  "source_url": "https://... | sql:q3 | policy:<chunk_id>",
  "source_type": "industry_report",
  "access": "full | partial | snippet",
  "published": "2026-03-23 or null",
  "period_covered": "2025-26 or null"
}
```

---

## 7. Accuracy rules (paywalls, citations, dates)

Core rule: **a claim can only be cited if the text supporting it was retrieved in this run.**

1. **Detect partial access** in `fetch_page`: short extracted body, paywall phrases ("subscribe to
   continue"), schema.org `isAccessibleForFree: false` in JSON-LD, HTTP 401/403. Returns
   `access: full | partial | blocked`.
2. **Verify evidence in code** (`evidence.verify`): the evidence span must appear (exact or close
   fuzzy match) in the stored text of that URL / query result, and every number in the claim must
   appear in the evidence. Failures are dropped and reported back to the Lead as rejected.
3. **Follow stories to primary sources.** Most paywalled articles report on something public.
4. **Snippet-only evidence** may support "X reported that…", labelled unverified, never for a
   number that isn't in the snippet. Single-source news claims are labelled as such.
5. **No paywall bypassing**: no archive sites, no bypass services.
6. **Dates:** use the page's publication metadata (trafilatura extracts it), not the date in the
   site header. Some sports sites print *today's* date in the header; during planning, search
   results showed years-old articles stamped with 2026 dates.
7. **Cache every fetched page** with a retrieval timestamp (reproducible traces, evidence checks,
   "retrieved on" labels).
8. **Cloud Run:** expect bot protection (403s) from Google Cloud IPs on pages that work locally.
   Test fetching from the deployed service early.
9. **Measure it:** `evals/citation_eval.py` runs ~15–20 questions and reports (a) deterministic
   checks: valid citation ids, every cited URL was fetched in `tool_calls`; (b) optional LLM judge:
   does the source text support the claim?

---

## 8. Questions the data can't answer (the agent must say so)

- **"Is School X Title IX compliant?"** EADA shows proportionality (prong 1). Prongs 2 (history
  of program expansion) and 3 (meeting the interests of the underrepresented sex) aren't in the
  data, and compliance is determined by OCR or courts, not by a number.
- **Individual athletes' NIL earnings** (private; only estimates exist).
- **Per-school revenue-sharing splits** unless the school has published or reported them.
- **Anything after the latest EADA year** for spending and participation.
- **Causation** ("did NIL cause schools to cut women's sports?") — only correlations and reported claims.

---

## 9. Sample questions (for README and testing)

Fill in expected answers once the data is loaded. For each README sample query we need: the
expected answer, the table/file/endpoint it comes from, the **no-tools, no-system-prompt** answer
from the same model, and our agent's answer.

1. *"How did Big Ten schools' spending on women's teams compare to men's in the latest EADA year,
   and what have they said about how they're splitting revenue-sharing money?"*
   → EADA expense views + news.
2. *"What share of revenue-sharing money is expected to go to women's sports, and how does that
   compare to women's share of athletes at Power 4 schools?"*
   → `nil_facts` + EADA participation view.
3. *"What's the proportionality gap at [specific school], and has it changed over the last five
   years?"* → EADA multi-year view; the agent should add the §8 compliance caveat.

Vague questions for the clarification requirement: *"How's women's basketball doing?"*,
*"Is my school fair to women?"*

Optional Text2SQL bonus: two questions where a plain `run_sql` agent (no mappings, no notes) gets
it wrong (e.g. summing duplicated participants; mismatching an institution name) and ours gets it
right, with both traces.

---

## 10. Repo layout (modelled on `wikipedia-deep-research`)

```
app.py              FastAPI: GET / , GET /run (SSE for the page), POST /chat (JSON), /context
research.py         the agents: Lead, three Researcher kinds, Writer; deep_research()
eada.py             read-only DuckDB access for the Data Researcher (replaces wikipedia.py's role)
web.py              search + fetch with paywall/access detection, dates, cache, source tiers
policy.py           local search over data/policy/*.md
evidence.py         evidence verification + citation checks (stdlib only; self-check in __main__)
context_panel.py    unchanged from the template: per-agent context timeline
context-panel.js    unchanged from the template
index.html          front end: what/who/how, examples, activity, rendered report, context panel
build_eada.py       OFFLINE: raw EADA files → data/eada.duckdb + data/eada_mappings.json
sql/views.sql       derived views (proportionality gap, per-athlete spending, ...) — fill in
data/               raw/ (gitignored), nil_facts.csv, column_notes.json, sources.json, policy/
evals/              questions.jsonl, citation_eval.py
Dockerfile, deploy.sh, .env.example, pyproject.toml
```

Stack (same as the course templates): Python ≥3.10, `uv`, OpenAI Agents SDK with LiteLLM → Vertex
AI Gemini (`gemini-3.5-flash`, `gemini-3.5-flash-lite`), FastAPI + SSE, DuckDB.

---

## 11. Build order (de-risked)

1. **Data first.** Download EADA files for ~5 recent years. `uv run scripts/build_eada.py --inspect`,
   read the data dictionary, fill in `FILE_TABLES` in `build_eada.py`, `sql/views.sql`, and
   `data/column_notes.json`. Build the DB.
2. **Data Researcher alone.** Get the Text2SQL part answering the trap questions correctly
   (temporarily call it directly). If everything else slips, this is still a solid project.
3. **Lead + Writer + Data Researcher** end to end, with evidence verification and `/chat`.
4. **Deploy to Cloud Run now**, before adding web search (timeouts and auth surprises show up here).
5. **Curated `nil_facts`**: verify seed rows against primary reports; add more.
6. **News Researcher** with access detection, caching, primary-source chasing.
7. **Policy corpus + Policy Researcher.**
8. **Front end polish** (rendered markdown, results table or chart), README, citation eval,
   no-tools comparison answers.

---

## 12. Open decisions

- Search API: Tavily (default) vs. Brave vs. Exa. Check free-tier limits.
- Host DuckDB in the image (simplest) vs. GCS (DuckDB `httpfs`) vs. BigQuery (more impressive).
- Whether to include IPEDS (graduation rates, enrollment by race) via `unitid`.
- Charts: markdown tables first; a chart (e.g. Vega-Lite spec emitted by the Writer) if time allows.
- Multi-turn follow-ups beyond clarification (currently each question is a fresh research run).

## 13. Risks

| Risk | Mitigation |
|---|---|
| EADA downloads unavailable or changed | Check first; fall back to the cutting-tool exports or a cached copy |
| Gemini structured output / parallel tool calls flaky via LiteLLM | Researchers fall back to parsing JSON from text; keep template's working settings |
| Cloud Run request timeout during long runs | SSE streaming, `--timeout 900`, researcher caps |
| Bot blocks / paywalls on news | Access flags, primary-source chasing, source list of open domains, honest "couldn't verify" |
| NIL figures misquoted | Curated table with provenance; evidence verification; `verified_against_primary` flag |
| Dates mislabelled | Metadata dates only; Writer labels every number with its period |
