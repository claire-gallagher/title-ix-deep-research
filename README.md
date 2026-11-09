# Title IX & NIL Deep Research

A deep research agent for **gender equity in college athletics**. Ask about participation,
spending and revenue at specific schools or conferences, or how revenue sharing and NIL are
changing the picture. A **Lead** decides whether the question needs clarifying, splits it into
tasks and sends **Data**, **News** and **Policy Researchers** off in parallel. Every finding they
return is checked in code against the text or query result it came from. A **Writer** turns the
verified findings into a report that cites every sentence, dates every number, keeps institutional
spending, revenue sharing and third-party NIL apart, and says what the data can't answer.

**Who it's for:** student journalists, athletes and recruits, Title IX coordinators, advocates.

> Design notes and decisions: [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md).

## Run it

```bash
cp .env.example .env                        # add VERTEXAI_PROJECT and TAVILY_API_KEY
gcloud auth application-default login
uv sync
uv run build_eada.py                        # once, offline (see data/README.md for the raw files)
uv run app.py                               # http://localhost:8000
```

`POST /chat` with `{"message": "...", "session_id": null}` returns
`{response, session_id, tool_calls}`; each tool call has an `agent` field (Lead, Data Researcher 2, ...).
If the response is a clarifying question, send the answer with the same `session_id`.

Live at: **TODO URL**

## Deploy (Cloud Run, from GitHub)

Every push to `main` builds with a buildpack and deploys, via Developer Connect (course guide
"Deploying to Cloud Run from GitHub"). The database isn't in git: it lives in a Cloud Storage bucket
mounted into the container.

1. Build the database locally (`uv run build_eada.py`) and upload it:
   `gcloud storage cp data/eada.duckdb gs://BUCKET/eada.duckdb`.
   Commit `data/eada_mappings.json` (small; the app reads it from the repo).
2. Store the search key: `printf '%s' "$TAVILY_API_KEY" | gcloud secrets create tavily-api-key --data-file=-`
3. Create the service as in the guide (buildpack, entrypoint
   `uvicorn app:app --host 0.0.0.0 --port $PORT`, IAP restricted to `columbia.edu`), then under
   *Containers, Volumes, Networking, Security* set:

| Setting | Value | Why |
|---|---|---|
| Volume | Cloud Storage bucket `BUCKET`, read-only, mounted at `/data` | the database |
| Env var `EADA_DB` | `/data/eada.duckdb` | where `eada.py` finds it |
| Env var `VERTEXAI_PROJECT` | your project ID | `.env` isn't deployed |
| Env var `CACHE_DIR` | `/tmp/cache` | writable location for the page cache |
| Secret `TAVILY_API_KEY` | `tavily-api-key:latest` | news search |
| Request timeout | 900 s | a research run takes minutes |
| Memory | 2 GiB | DuckDB |

The service account needs `roles/aiplatform.user` (Gemini) and `roles/secretmanager.secretAccessor`;
the console offers to grant the secret role when you add it. To update the data, upload a new
`eada.duckdb` and deploy a new revision; no push needed.

## Data

| Source | What | How it's loaded |
|---|---|---|
| [EADA data files](https://ope.ed.gov/athletics/#/datafile/list), U.S. Dept. of Education | Participation, enrollment, spending and revenue by school, sport, gender and year | `build_eada.py` → `data/eada.duckdb` (tables `institutions`, `sports`, views `v_proportionality`, `v_spending_by_gender`) |
| `data/nil_facts.csv` | Curated NIL / revenue-sharing figures (Opendorse reports, College Sports Commission NIL Go reports), with provenance | loaded as table `nil_facts` |
| `data/policy/` | Primary documents: Title IX regulations, three-part test, House v. NCAA settlement terms | searched by `policy.py` |
| Web (Tavily) | Recent news and reports | `web.py`, cached with retrieval time |

TODO: years loaded, row counts, data dictionary link.

## Architecture

TODO: diagram (see PROJECT_BRIEF.md §6) and a short trace walkthrough.

## Sample queries

TODO: for each, fill in the expected answer and where it comes from, then paste the answer from the
same model with **no tools and no system prompt**, and our agent's answer.

### 1. TODO question

- **Expected answer:** TODO
- **Source:** TODO (table / file / endpoint)
- **No-tools answer (gemini-3.5-flash, no system prompt):** TODO
- **Agent answer:** TODO

### 2. TODO question

- **Expected answer:** TODO
- **Source:** TODO
- **No-tools answer:** TODO
- **Agent answer:** TODO

### 3. TODO question

- **Expected answer:** TODO
- **Source:** TODO
- **No-tools answer:** TODO
- **Agent answer:** TODO

## Clarification, gaps and limits

TODO: example of a vague question and the clarifying question it gets; example of the Lead going
back for more; example of a question the data can't answer.

## Citation accuracy

`uv run evals/citation_eval.py` against a running server. TODO: results table.

## Optional: plain `run_sql` vs. our Data Researcher

TODO: two questions a plain run_sql agent gets wrong and ours gets right, with both traces.
