"""The research team:

    Lead plans -> Data, Policy, News Researchers (in parallel) -> evidence check
               -> Lead reviews: back for what's missing (one more round at most) -> Writer

Built from wikipedia-deep-research's pieces. What's different:
- The Lead works in structured calls rather than a tool loop: a plan (a clarifying question, or a
  task for each researcher the question needs, sent at once), then a review of the verified
  findings and gaps that sends follow-up tasks for just what's missing, up to MAX_ROUNDS. Each call
  can be evaluated on its own (evals/: plan and review, data researcher, end to end).
- Three researchers with different tools, each in a fresh context that sees only its task: Data
  (SQL over EADA + curated figures, with only the part of the schema its task needs), Policy
  (primary legal documents), News (web search and fetch).
- Researchers return structured findings, each with the evidence span it rests on. Every one is
  checked with evidence.verify() before the Lead or Writer sees it; a researcher gets one turn to
  repair rejected findings.
- The Writer cites finding ids ([F3]); code renumbers them per source ([1], [2]...) and builds the
  Sources list from what was retrieved, so a citation can only point at something this run read.
- A follow-up question in the same conversation starts from the findings already gathered.
"""

import asyncio
import contextvars
import html
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date

from agents import Agent, AgentOutputSchema, ModelSettings, RunContextWrapper, Runner, function_tool
from agents.extensions.models.litellm_model import LitellmModel
from pydantic import BaseModel

import eada
import evidence
import policy
import runlog
import web
from context_panel import PanelHooks

# LiteLLM routes the call to Gemini. api_key is unused: Vertex AI uses your gcloud credentials.
MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash", api_key="unused")
# Researchers read and take notes: the cheaper model is enough.
RESEARCHER_MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash-lite", api_key="unused")
# A stalled model call would hang the page: give up after 90 s. Retries (with backoff) mostly
# absorb Vertex's 429 RESOURCE_EXHAUSTED, which the shared Gemini quota returns under bursts.
SETTINGS = ModelSettings(extra_args={"vertex_location": "global", "timeout": 90, "num_retries": 5})

# Research rounds per question: the first, plus one follow-up the Lead sends when its review finds a
# gap. Earlier, a Lead in a tool loop sent follow-ups freely and they mostly repeated the first
# researcher's work (a second News Researcher re-found the ESPN deal); now the review sees every
# verified finding and asks only for what's missing, once.
MAX_ROUNDS = 2
# Turns per researcher: each turn resends its whole conversation, so turns are what cost time.
RESEARCHER_TURNS = 10
# SQL queries per Data Researcher. The prompt says so too, but flash-lite has re-run the same
# successful query a dozen times; code enforces it.
MAX_QUERIES_PER_RESEARCHER = 8
# Web searches per question: each one spends a Tavily credit.
MAX_SEARCHES = 4
# Set per researcher in run_researcher(); its tools and instructions read them.
_researcher_queries: contextvars.ContextVar[list | None] = contextvars.ContextVar("researcher_queries", default=None)
_researcher_task: contextvars.ContextVar[str] = contextvars.ContextVar("researcher_task", default="")


# --- Shared state ---


class ResearchHooks(PanelHooks):
    """The template's PanelHooks, plus a record of every tool call for /chat's tool_calls.
    Each call carries the agent that made it, so sub-agents' calls are distinguishable."""

    def __init__(self, contexts, emit):
        super().__init__(contexts, emit)
        self.tool_calls: list[dict] = []

    @staticmethod
    def _args(context) -> dict | str:
        raw = getattr(context, "tool_arguments", None)
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return raw

    # 'play' events drive the page's play diagram: each tool call is a pass out and back, with what
    # was asked and how it came back, so you can see which agent fumbles without opening the log.
    async def on_tool_start(self, context, agent, tool):
        await super().on_tool_start(context, agent, tool)
        self.play_start(agent.name, tool.name, getattr(context, "tool_call_id", None), _play_detail(tool.name, self._args(context)))

    async def on_tool_end(self, context, agent, tool, result):
        self.record(agent.name, tool.name, self._args(context), str(result), getattr(context, "tool_call_id", None))

    def play_start(self, agent: str, tool: str, call_id, detail: str) -> None:
        self.emit({"type": "play", "phase": "start", "agent": agent, "tool": tool, "call_id": call_id, "detail": detail})

    def record(self, agent: str, tool: str, args, result: str, call_id) -> None:
        self.tool_calls.append({"agent": agent, "name": tool, "args": args, "result": result[:4000]})
        outcome, note = _play_outcome(tool, result)
        self.emit({"type": "play", "phase": "end", "agent": agent, "tool": tool, "call_id": call_id, "outcome": outcome, "note": note})


_PLAY_ARG = {"run_sql": "query", "search_web": "query", "fetch_page": "url", "search_policy": "query",
             "read_policy": "chunk_id", "find_columns": "query", "research": "task"}
_REFUSALS = ("already ran this exact query", "same rows as", "limit reached")


def _play_detail(tool: str, args) -> str:
    """The one thing worth showing about a call: the query, the URL, the task."""
    if not isinstance(args, dict):
        return str(args)[:300]
    if tool == "get_enum_values":
        return f"{args.get('table')}.{args.get('column')}"
    text = str(args.get(_PLAY_ARG.get(tool, ""), "") or json.dumps(args))
    if tool == "research":
        text = f"{args.get('kind')}: {text}"
    return " ".join(text.split())[:300]


def _play_outcome(tool: str, result: str) -> tuple[str, str]:
    """('ok' | 'error' | 'refused', a short note) for one tool result."""
    try:
        data = json.loads(result)
    except ValueError:
        return "ok", ""
    if not isinstance(data, dict):
        return "ok", f"{len(data)} results" if isinstance(data, list) else ""
    if "error" in data:
        kind = "refused" if any(r in data["error"] for r in _REFUSALS) else "error"
        return kind, str(data["error"])[:160]
    if "rows" in data:
        return "ok", f"{data.get('query_id')}: {len(data['rows'])} rows"
    if "access" in data:
        return "ok", f"{data['access']} access"
    if "verified_findings" in data:
        return "ok", f"{len(data['verified_findings'])} verified, {len(data.get('rejected', []))} rejected"
    if "full_text" in data:
        return "ok", f"{len(data['full_text']) + len(data.get('more', []))} passages"
    return "ok", ""


@dataclass
class Research:
    """One question's state, shared by every agent and tool in it. The model never sees it."""

    hooks: ResearchHooks
    plans: list[dict] = field(default_factory=list)  # the Lead's plan, then each review's follow-ups (run log)
    researchers: int = 0
    queries: int = 0
    searches: int = 0
    searches_cached: int = 0  # searches answered from the 24 h cache: no Tavily credit spent
    policy_searches: int = 0
    repairs: list[dict] = field(default_factory=list)  # first-try rejections and how many the repair turn fixed
    usage: dict[str, dict] = field(default_factory=dict)  # agent name -> requests and tokens
    findings: list[dict] = field(default_factory=list)  # verified only, ids F1..Fn, for the Writer
    rejected: list[dict] = field(default_factory=list)  # failed evidence checks, with the reason
    gaps: list[str] = field(default_factory=list)
    # What this conversation actually retrieved: evidence.verify() checks findings against these
    pages: dict[str, dict] = field(default_factory=dict)  # url or 'policy:<chunk>' -> {text, access, ...}
    sql_results: dict[str, dict] = field(default_factory=dict)  # 'q3' -> {query, columns, rows}
    history: list[dict] = field(default_factory=list)  # earlier questions in this conversation and their answers

    def follow_up(self, hooks: "ResearchHooks") -> "Research":
        """State for the next question in a conversation: the evidence carries over (findings keep
        their ids and still verify against the same pages and query results; new ones number on);
        the per-question budgets start again."""
        return Research(hooks=hooks, queries=self.queries, findings=self.findings, rejected=self.rejected,
                        pages=self.pages, sql_results=self.sql_results, history=self.history)


class Finding(BaseModel):
    claim: str
    evidence_text: str
    source_url: str
    source_type: str
    access: str
    published: str | None
    period_covered: str | None


class Findings(BaseModel):
    findings: list[Finding]
    gaps: list[str]


class Plan(BaseModel):
    clarify: str | None = None  # one short question for the user, instead of researching
    data_task: str | None = None  # numbered questions for the Data Researcher, or null
    policy_task: str | None = None  # ... for the Policy Researcher
    news_task: str | None = None  # ... for the News Researcher
    # Review only: the parts of the question, in its own words, that the findings don't answer.
    # Follow-up tasks without one are dropped in code (see review()).
    unanswered: list[str] | None = None


def _record_usage(state: "Research", agent_name: str, result) -> None:
    """Add one Runner.run's model usage to the run's totals, per agent (for the run log)."""
    u = result.context_wrapper.usage
    entry = state.usage.setdefault(agent_name, {"requests": 0, "input_tokens": 0, "output_tokens": 0})
    entry["requests"] += u.requests
    entry["input_tokens"] += u.input_tokens
    entry["output_tokens"] += u.output_tokens


def _error(e: Exception) -> str:
    # The model cannot see an exception. Return something it can reason about.
    return json.dumps({"error": f"{type(e).__name__}: {str(e)[:300]}"})


# --- Data tools (EADA + nil_facts). The schema for the task is in the Data Researcher's instructions. ---


@function_tool
def get_enum_values(table: str, column: str) -> str:
    """Get the exact valid values of an enum column. Match them exactly: equality is case-sensitive.

    Args:
        table: The table name, e.g. 'institutions'.
        column: The column name.
    """
    try:
        return json.dumps(eada.enum_values(table, column), default=str)
    except Exception as e:
        return _error(e)


@function_tool
def find_columns(query: str) -> str:
    """Find columns your schema left out: searches every table's column names and descriptions.
    Returns matching columns with descriptions, and the notes that apply to them.

    Args:
        query: A few words, e.g. 'athletic aid' or 'assistant coach salary'.
    """
    try:
        return json.dumps(eada.find_columns(query), default=str)
    except Exception as e:
        return _error(e)


def _same_result(done: dict[str, dict], result: dict) -> str | None:
    """The id of an earlier query with the same columns and rows (in any order), if there is one."""
    def key(r):
        return sorted(json.dumps(row, default=str) for row in r.get("rows", []))
    for qid, earlier in done.items():
        if earlier.get("columns") == result.get("columns") and key(earlier) == key(result):
            return qid
    return None


@function_tool
def run_sql(ctx: RunContextWrapper[Research], query: str) -> str:
    """Run a read-only DuckDB SQL query. Returns {query_id, columns, rows, truncated}.
    Cite a result in a finding as source_url 'sql:<query_id>'.

    Args:
        query: A DuckDB SQL query.
    """
    normalized = " ".join(query.split()).rstrip(";").lower()
    for qid, done in ctx.context.sql_results.items():
        if " ".join(done["query"].split()).rstrip(";").lower() == normalized:
            return json.dumps({"error": f"You already ran this exact query: its result is {qid}, above. "
                               "Don't run it again. Write your findings from it, citing sql:" + qid})
    mine = _researcher_queries.get()
    if mine is not None and len(mine) >= MAX_QUERIES_PER_RESEARCHER:
        return json.dumps({"error": f"Query limit reached ({MAX_QUERIES_PER_RESEARCHER}). Write your findings "
                           "now from the results you have, and list what's missing in gaps."})
    try:
        result = eada.run_sql(query)
    except Exception as e:
        return _error(e)
    if "error" in result:
        return json.dumps(result)
    # Re-running a query with an alias, a NOT NULL filter or a different ORDER BY returns the same
    # rows: a Data Researcher once did this six times instead of writing its findings. Compare
    # results, not query text.
    same = _same_result(ctx.context.sql_results, result)
    if same:
        return json.dumps({"error": f"This returns the same rows as {same}, which you already have. "
                           f"Write your findings from {same} now, citing sql:{same}."})
    ctx.context.queries += 1
    query_id = f"q{ctx.context.queries}"
    ctx.context.sql_results[query_id] = {"query": query, **result}
    if mine is not None:
        mine.append(query_id)
    return json.dumps({"query_id": query_id, **result}, default=str)


# --- Sources tools: policy corpus and web ---


POLICY_FULL_HITS = 3  # top search hits returned in full: most tasks need no separate read
# Policy searches per question. A researcher once searched nine times with ever more specific legal
# terms and ran out of turns: plain words find more, and past this it should write up.
MAX_POLICY_SEARCHES = 5


def _retrieve_chunk(state: Research, chunk: dict) -> dict:
    """Record a chunk as retrieved (so findings can cite it) and return it as the model sees it."""
    chunk_id = chunk["chunk_id"]
    state.pages[f"policy:{chunk_id}"] = {
        "text": chunk["text"], "access": "full", "published": chunk.get("published"),
        "title": chunk.get("title"), "source_type": "policy_document", "document_url": chunk.get("source_url"),
    }
    # source_url must be the 'policy:' id: it's the key evidence.verify() looks up. The document's
    # web address goes under its own name (it used to overwrite source_url, and every policy
    # finding was then rejected as "not retrieved").
    return {
        "source_url": f"policy:{chunk_id}", "document_url": chunk.get("source_url"),
        **{k: chunk.get(k) for k in ("title", "published", "notes", "text")},
    }


@function_tool
def search_policy(ctx: RunContextWrapper[Research], query: str) -> str:
    """Search primary policy documents (Title IX statute and regulations, the three-part test,
    OCR guidance, House v. NCAA settlement terms, College Sports Commission rules, the EADA user's
    guide). The best matches come back in full, ready to cite as their source_url; the rest as
    snippets you can open with read_policy.

    Args:
        query: Plain words, as the document would put them, e.g. 'three-part test substantially
            proportionate' or 'total damages paid to former athletes'.
    """
    if ctx.context.policy_searches >= MAX_POLICY_SEARCHES:
        return json.dumps({"error": f"Policy search limit reached ({MAX_POLICY_SEARCHES}). Write your findings from the "
                           "passages you have, and list what's missing in gaps."})
    ctx.context.policy_searches += 1
    hits = policy.search(query)
    full = [_retrieve_chunk(ctx.context, policy.read(h["chunk_id"])) for h in hits[:POLICY_FULL_HITS]]
    return json.dumps({"full_text": full, "more": hits[POLICY_FULL_HITS:]})


@function_tool
def read_policy(ctx: RunContextWrapper[Research], chunk_id: str) -> str:
    """Read one more policy chunk in full. Cite it as source_url 'policy:<chunk_id>'.

    Args:
        chunk_id: A chunk_id from search_policy, e.g. 'title-ix-regulation-34-cfr-106-41#0'.
    """
    chunk = policy.read(chunk_id)
    if chunk is None:
        return json.dumps({"error": f"No chunk '{chunk_id}'. Use a chunk_id from search_policy."})
    return json.dumps(_retrieve_chunk(ctx.context, chunk))


@function_tool
def search_web(ctx: RunContextWrapper[Research], query: str) -> str:
    """Search the web for recent news and reports. Returns titles, URLs, snippets, dates and each
    domain's source type. Snippets are not articles: fetch a page before relying on it.

    Args:
        query: A short search query, 2-8 words.
    """
    if not os.environ.get("TAVILY_API_KEY"):
        return json.dumps({"error": "Web search is unavailable (no search API key). Use the policy documents."})
    if ctx.context.searches >= MAX_SEARCHES:
        return json.dumps({"error": f"Search limit reached ({MAX_SEARCHES} per question). Work with the pages you have: fetch the most promising results, or report what you found."})
    ctx.context.searches += 1
    try:
        results, from_cache = web.search(query)
    except Exception as e:
        return _error(e)
    ctx.context.searches_cached += from_cache  # the rest spent a Tavily credit
    for r in results:
        # A snippet is evidence too, but only for what it says, and labelled as such
        ctx.context.pages.setdefault(r["url"], {"text": r["snippet"], "access": "snippet", "published": r["published"], "title": r["title"], "source_type": r["source_type"]})
    return json.dumps(results)


@function_tool
def fetch_page(ctx: RunContextWrapper[Research], url: str) -> str:
    """Fetch a web page's text. Returns its title, publication date (from page metadata), access
    ('full', 'partial' for paywalls and teasers, 'blocked'), and text.

    Args:
        url: A URL from search results or from a page you fetched.
    """
    try:
        page = web.fetch(url)
    except Exception as e:
        return _error(e)
    blocked = page["access"] == "blocked"
    if not (blocked and url in ctx.context.pages):  # a blocked fetch doesn't erase a usable snippet
        ctx.context.pages[url] = {
            "text": page["full_text"], "access": page["access"], "published": page["published"],
            "title": page["title"], "source_type": page.get("source_type"), "retrieved_at": page["retrieved_at"],
        }
    keys = ("url", "title", "published", "access", "access_reason", "source_type", "retrieved_at", "text")
    return json.dumps({k: page.get(k) for k in keys})


# --- The researchers ---

FINDINGS_RULES = """\
Reply with findings only, as JSON: {"findings": [...], "gaps": [...]}. Each finding:
  claim           one fact, in your words, that answers part of the task
  evidence_text   copied EXACTLY from what a tool returned (a sentence of a page, or the values of a
                  query result with their column names). Never paraphrase it. Every number in the
                  claim must appear in evidence_text, years included.
  source_url      where the evidence came from, as given by the tool
  source_type     official_filing, official_body, policy_document, industry_report, legal_analysis,
                  news, or estimate
  access          full, partial or snippet, as the tool reported it
  published       the publication date the tool reported, or null. Never a date from a site header.
  period_covered  the period the fact is about, e.g. '2023-24', or null
At most 8 findings, only ones that answer the task. A trend or a list is ONE finding: the series in
the claim ("rose from 1.98M in 2021-22 to 2.48M in 2023-24"), every value in evidence_text. Never
spend findings one per row: rows past the limit are lost, and the latest years matter most.
Findings are checked in code against what the tools returned; ones that don't match are thrown
away. In gaps, list the parts of the task the sources didn't answer."""

DATA_PROMPT = f"""\
You answer research questions with SQL over a DuckDB database:
- EADA (U.S. Equity in Athletics Disclosure Act) data: participation, enrollment, coaching, spending
  and revenue by school, sport, gender and year, plus each school's conference (from IPEDS).
  Official filings (source_type official_filing).
- nil_facts: curated NIL and revenue-sharing figures from reports, one row per figure, with its
  source, period, method note and URL. Use each row's own source_type.

Below is the part of the schema your task needs, and the gotchas: read them, they describe mistakes
that give wrong answers. find_columns finds any column left out.

1. Answer each question with as few queries as you can: one query can return several years,
   schools or measures. You can run independent queries in the same turn.
2. Resolve school names through institution_aliases (ILIKE), then filter on unitid.
3. Before filtering on a text column whose values you haven't seen, call get_enum_values. Never guess values.
4. Prefer the derived views (v_proportionality, v_spending_by_gender) to re-deriving metrics.
5. Always SELECT survey_year (EADA) or period_covered and source_org (nil_facts), so the evidence
   carries the period, and a count of schools for any average or total, so the scope is visible.
   For nil_facts, keep the method_note: it says what the number measures.
6. Every number in a finding must appear in the ONE query result it cites, or the finding is thrown
   away. So compute differences, totals, ratios and percentages in SQL, not in your head, and put
   the school count in the same SELECT as the figures it qualifies. Don't add numbers from a second
   query to a finding: make it its own finding.
7. A trend is one finding per measure, all years in it. Claim: "The University of X's proportionality
   gap rose from -0.2 points in 2015-16 to 7.8 in 2024-25 (0.3 in 2016-17, ..., 8.8 in 2023-24)."
   evidence_text: "survey_year=2016, gap_pp=-0.2; survey_year=2017, gap_pp=0.3; ...; survey_year=2025, gap_pp=7.8".
8. Run at most {MAX_QUERIES_PER_RESEARCHER} queries, and never the same query twice: read the result you have.
   If the data can't answer part of the task, say so in gaps: EADA has no NIL deals, and its latest
   year (2024-25) predates revenue sharing.

{FINDINGS_RULES}
For SQL findings, source_url is 'sql:<query_id>' and evidence_text lists the values, e.g.
"survey_year=2023, institution_name=University of X, female_athlete_pct=47.2"."""


def data_instructions(ctx, agent) -> str:
    # Only the part of the schema this task needs (see eada.schema_for): the whole schema is ~25k
    # characters, resent on every turn. Enum values stay behind get_enum_values.
    try:
        schema = eada.schema_for(_researcher_task.get())
    except Exception as e:  # no database: the researcher can still say so
        schema = f"(database unavailable: {e})"
    return f"{DATA_PROMPT}\n\nSCHEMA FOR THIS TASK:\n{schema}"


data_researcher = Agent(
    name="Data Researcher",
    instructions=data_instructions,
    model=RESEARCHER_MODEL,
    model_settings=SETTINGS,
    tools=[get_enum_values, find_columns, run_sql],
    output_type=AgentOutputSchema(Findings, strict_json_schema=False),
)

news_researcher = Agent(
    name="News Researcher",
    instructions=f"""\
You answer research questions with recent news and reports from the web: what happened after the
data and the legal documents, or what they don't cover (revenue-sharing splits, lawsuits,
conference decisions, NIL market figures).

1. Search with search_web: at most 3 searches, each aimed at a question, not repeated with small
   wording changes: each costs money. Prefer official bodies, the organisations that published a
   report, and open news sites to paywalled ones. Fetch the most relevant pages (at most 3) in one turn.
2. Follow stories to primary sources: when an article reports on a report, filing, press release or
   court document, find and fetch that, and cite it instead of the article.
3. If a page is 'partial' or 'blocked' (paywall, bot check), don't guess what it says. Use another
   source, or the search snippet with access 'snippet'. Never use archive sites or paywall workarounds.
4. Use the publication date the tool reports. If it's null, published is null: never take the date
   printed in a site's header, which is often today's date.
5. Opendorse, On3 and similar companies sell NIL services: their figures are industry_report or
   estimate, not official numbers. Say what each figure measures (deals on one platform, a projection...).

{FINDINGS_RULES}""",
    model=RESEARCHER_MODEL,
    model_settings=SETTINGS,
    tools=[search_web, fetch_page],
    output_type=AgentOutputSchema(Findings, strict_json_schema=False),
)

policy_researcher = Agent(
    name="Policy Researcher",
    instructions=f"""\
You answer research questions from primary policy documents: the Title IX statute and athletics
regulations, the three-part test, Department of Education guidance, House v. NCAA settlement terms
and the court's final approval order, College Sports Commission / NIL Go rules, the EADA user's guide.

1. Search with search_policy in plain words, as the document would put them ("total damages paid to
   former athletes", not a string of legal terms). Its best matches come back in full; open another
   passage with read_policy only if those don't answer the question.
2. Quote the rule itself. Don't interpret beyond what the text says; note where it's ambiguous.
3. Some documents are rescinded or superseded (their title or notes say so): say that in the claim.

{FINDINGS_RULES}
source_type is policy_document; source_url is the 'policy:<chunk_id>' the search returned.""",
    model=RESEARCHER_MODEL,
    model_settings=SETTINGS,
    tools=[search_policy, read_policy],
    output_type=AgentOutputSchema(Findings, strict_json_schema=False),
)

RESEARCHERS = {"data": data_researcher, "news": news_researcher, "policy": policy_researcher}


def _parse(output) -> tuple[list[dict], list[str]]:
    """A researcher's output -> (findings, gaps). Tolerates JSON in text if structured output fails."""
    if isinstance(output, Findings):
        return [f.model_dump() for f in output.findings], output.gaps
    match = re.search(r"\{.*\}", str(output), re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        data = {}
    return data.get("findings", []), data.get("gaps", [])


def _latest_rows_left_out(accepted: list[dict], state: Research) -> dict | None:
    """The latest years of a query the researcher cited but didn't write up, as a finding built in
    code from the rows. Researchers sometimes spend one finding per year, hit the limit, and drop
    2023-24 and 2024-25; the report then said EADA "only covers up to 2022-23". Built from the
    query result itself, so evidence.verify() passes it like any other."""
    for f in accepted:
        url = f.get("source_url", "")
        result = state.sql_results.get(url[4:]) if url.startswith("sql:") else None
        if not result or "survey_year" not in result.get("columns", []):
            continue
        col = result["columns"].index("survey_year")
        cited = [g for g in accepted if g.get("source_url") == url]
        covered = {int(y) for g in cited for y in re.findall(r"survey_year=(\d{4})", g.get("evidence_text", ""))}
        # Years named in the claim: evidence.numbers() reads "2024-25" as survey year 2025
        covered |= {int(n) for g in cited for n in evidence.numbers(g.get("claim", "") + " " + (g.get("period_covered") or ""))
                    if n.isdigit() and 2000 <= int(n) <= 2099}
        if not covered:
            continue
        later = [row for row in result["rows"] if isinstance(row[col], int) and row[col] > max(covered)]
        if not later:
            continue
        evidence_text = "; ".join(", ".join(f"{c}={v}" for c, v in zip(result["columns"], row)) for row in later)
        years = ", ".join(f"{row[col] - 1}-{str(row[col])[2:]}" for row in later)
        return {"claim": f"The same query's later years ({years}), which the researcher didn't write up: {evidence_text}",
                "evidence_text": evidence_text, "source_url": url, "source_type": "official_filing", "access": "full",
                "published": None, "period_covered": years, "check": "rows of the cited query, added in code"}
    return None


_WRITE_UP_NOW = ("Stop using tools. Reply now with your findings as JSON ({\"findings\": [...], \"gaps\": [...]}), "
                 "from what the tools have already returned. List what you couldn't find in gaps.")


def _write_up_at_turn_limit(agent: Agent, state: Research):
    """A max_turns handler: instead of losing everything the researcher read (a policy researcher
    once searched nine times, hit the limit, and returned nothing), one last tool-free call writes
    up its findings from the conversation so far."""
    async def handler(data):
        writer_only = agent.clone(tools=[])
        history = list(data.run_data.history) + [{"role": "user", "content": _WRITE_UP_NOW}]
        result = await Runner.run(writer_only, history, context=state, max_turns=1)
        _record_usage(state, agent.name, result)
        return {"final_output": result.final_output, "include_in_history": True}
    return handler


async def _repair(agent: Agent, result, rejected: list[dict], state: Research) -> list[dict]:
    """One tool-free turn: tell the researcher why findings failed, get corrected ones back."""
    reasons = "\n".join(f"- claim: {r['claim']}\n  source_url: {r['source_url']}\n  rejected because: {r['reason']}" for r in rejected)
    message = (f"The evidence check rejected these findings:\n{reasons}\n\nReply with corrected versions of them only, as "
               "JSON ({\"findings\": [...], \"gaps\": []}). Fix each so every number in its claim appears in its "
               "evidence_text, copied exactly from a tool result: drop numbers you worked out yourself, or split a finding "
               "that mixes two query results. Cite policy documents by their 'policy:...' source_url. Leave out a finding "
               "you can't fix.")
    try:
        repair = await Runner.run(agent.clone(tools=[]), result.to_input_list() + [{"role": "user", "content": message}],
                                  context=state, max_turns=1)
    except Exception:
        return []
    _record_usage(state, agent.name, repair)
    return _parse(repair.final_output)[0]


async def run_researcher(kind: str, task: str, state: Research, round_no: int = 1) -> dict:
    """Run one researcher on its task (in a fresh context: it sees only the task), verify its
    findings, and add the verified ones to the state. A follow-up round's researcher gets its own
    name ("Data Researcher 2"), so the trace, scorecard and board keep the two apart.
    Returns {verified_findings, rejected, gaps} (the Lead's 'research' call in the run log)."""
    hooks = state.hooks
    agent = RESEARCHERS[kind] if round_no == 1 else RESEARCHERS[kind].clone(name=f"{RESEARCHERS[kind].name} {round_no}")
    call_id = f"plan-{kind}-{round_no}"
    # The board draws the Lead handing this task over, as it did when the Lead called a tool
    hooks.play_start("Lead", "research", call_id, _play_detail("research", {"kind": kind, "task": task}))
    state.researchers += 1
    _researcher_queries.set([])  # this researcher's own query count (each gather task has its own context)
    _researcher_task.set(task)  # read by data_instructions() to size the schema
    try:
        result = await Runner.run(agent, f"Task: {task}", context=state, hooks=hooks, max_turns=RESEARCHER_TURNS,
                                  error_handlers={"max_turns": _write_up_at_turn_limit(agent, state)})
    except Exception as e:
        hooks.record("Lead", "research", {"kind": kind, "task": task}, _error(e), call_id)
        return {"error": str(e)}
    _record_usage(state, agent.name, result)
    findings, gaps = _parse(result.final_output)

    accepted, rejected = [], []

    def check(batch: list[dict]) -> list[dict]:
        """Verify a batch: accepted ones join the state; returns the rejected ones."""
        failed = []
        seen = {(f.get("source_url"), " ".join(str(f.get("claim", "")).split()).lower()) for f in state.findings}
        for finding in batch:
            if (finding.get("source_url"), " ".join(str(finding.get("claim", "")).split()).lower()) in seen:
                continue  # a repair turn re-sending a finding already accepted
            evidence.resolve_source(finding, state.pages)
            ok, reason = evidence.verify(finding, state.pages, state.sql_results)
            if ok:
                finding.update(id=f"F{len(state.findings) + 1}", task=task, researcher=agent.name, check=reason)
                state.findings.append(finding)
                accepted.append(finding)
            else:
                failed.append({"claim": finding.get("claim"), "source_url": finding.get("source_url"), "reason": reason,
                               "evidence_text": finding.get("evidence_text"), "researcher": agent.name})
        return failed

    rejected = check(findings)
    if rejected:
        # One repair turn: the researcher sees why each finding failed and fixes or drops it. Most
        # rejections are a sound finding with one number the model worked out itself ("rose 25.46%");
        # without this, the whole finding (and its correct numbers) was lost. Repairs face the same check.
        repaired = await _repair(agent, result, rejected, state)
        before = len(accepted)
        still = check(repaired)
        hooks.activity(agent.name, f"repaired {len(accepted) - before} of {len(rejected)} rejected findings")
        state.repairs.append({"researcher": agent.name, "first_try": rejected, "repaired": len(accepted) - before})
        rejected = still if repaired else rejected
    left_out = _latest_rows_left_out(accepted, state)
    if left_out:
        left_out.update(id=f"F{len(state.findings) + 1}", task=task, researcher=f"{agent.name} (rows added in code)")
        state.findings.append(left_out)
        accepted.append(left_out)
    state.rejected += rejected
    state.gaps += gaps
    hooks.activity(agent.name, f"{len(accepted)} findings verified, {len(rejected)} rejected")

    keys = ("id", "claim", "source_type", "access", "period_covered")
    brief_rejected = [{k: r[k] for k in ("claim", "source_url", "reason")} for r in rejected]  # the log keeps the rest
    summary = {"verified_findings": [{k: f.get(k) for k in keys} for f in accepted], "rejected": brief_rejected, "gaps": gaps}
    hooks.record("Lead", "research", {"kind": kind, "task": task}, json.dumps(summary), call_id)
    return summary


# --- The lead: plans, then reviews the findings once and goes back for what's missing ---

RESEARCHER_LIST = """\
- data: SQL over EADA, the federal Equity in Athletics data (participation, enrollment, spending,
  revenue and coaching by school, sport, gender and year, 2015-16 through 2024-25; NCAA division and
  conference per school), plus a curated table of NIL, revenue-sharing and House settlement figures.
  All numbers about schools come from here.
- policy: primary legal documents: Title IX regulations, the three-part test, House v. NCAA
  settlement terms and the court's final approval order, NIL Go rules, the EADA user's guide.
- news: web search, for what happened after the data or what it doesn't cover (schools'
  revenue-sharing splits, lawsuits, investigations, conference decisions, NIL market reports)."""

LEAD_PROMPT = f"""\
You plan research on gender equity in college athletics: participation, spending and revenue by
school and gender, and how revenue sharing and NIL are changing them. Today is {{today}}.
You don't research yourself: you write tasks for three researchers, who work in parallel, each in a
fresh context that sees only its task.

{RESEARCHER_LIST}

Reply with JSON: {{"clarify": ..., "data_task": ..., "policy_task": ..., "news_task": ...}}
1. If the question needs a school, conference, sport, year range or measure and doesn't say, or is
   too broad to answer well, set clarify to one short question offering 2-3 concrete options, and
   every task to null. Ask once at most: if the message includes the user's answer to a clarifying
   question, plan, choosing sensible defaults.
2. Otherwise give each researcher the question needs a short numbered list of specific questions,
   naming the schools, conference, division, sport, years and measures. Leave the others null.
   Send every researcher that has a part of the answer, all at once, and ask only for what answers
   the question: don't widen it (a compliance question doesn't need coach salaries; a question only
   about numbers in EADA doesn't need background on how EADA defines revenue).
   Default to the latest EADA year (2024-25) and, for trends, the full range back to 2015-16.
3. Keep the kinds of money apart, and name the one the question is about: school athletics spending
   (EADA), revenue sharing (school to current athletes, from 2025-26), third-party NIL, and House
   settlement back-pay damages to former athletes.
4. Don't ask for what no source has: whether a school is legally compliant with Title IX, or what an
   individual athlete earns. The writer will say so.
5. Follow-up questions list the findings this conversation already has. Ask only for what they
   don't cover; if they answer the question, set every task to null."""

REVIEW_PROMPT = f"""\
You lead a research team on gender equity in college athletics. Today is {{today}}. Your researchers
have reported back; you decide whether to send them back for what's missing.

{RESEARCHER_LIST}

You get the question, the tasks you sent, the verified findings (claims that passed an evidence
check), the gaps the researchers reported, and findings the check rejected.

Reply with JSON: {{"unanswered": [...], "data_task": ..., "policy_task": ..., "news_task": ...}}
First fill "unanswered": the parts of the question, quoted in the question's own words, that the
verified findings don't answer. A gap a researcher reported about something the question didn't ask
(coach salaries, for a compliance question; OCR's exact tolerance) is not a part of the question:
leave it out. If the list is empty, every task must be null.
Judge against the question the user asked, not against the tasks you sent: a task part that came
back empty needs no follow-up if the question is answered without it.
- If a part of the QUESTION is still unanswered (a number it asks for, a rule it asks about), and a
  researcher could plausibly find it, set that researcher's task to a short numbered list of
  questions for just that part, saying what was already found so it isn't repeated. Name a
  different source or angle than the first try when the first try came back empty.
- Otherwise set every task to null: when the findings answer the question, when what's missing is
  extra detail or context the question didn't ask for, or when it's something no source has (a
  school's legal compliance, an individual's earnings, EADA data after 2024-25). This is the usual
  answer: a second round costs as much as the first."""


def lead_instructions(ctx, agent) -> str:
    return LEAD_PROMPT.replace("{today}", date.today().isoformat())


def review_instructions(ctx, agent) -> str:
    return REVIEW_PROMPT.replace("{today}", date.today().isoformat())


lead = Agent(
    name="Lead",
    instructions=lead_instructions,
    model=MODEL,
    model_settings=SETTINGS,
    output_type=AgentOutputSchema(Plan, strict_json_schema=False),
)
# The same Lead, reviewing: one call after each round, deciding whether to go back for more
lead_review = Agent(
    name="Lead",
    instructions=review_instructions,
    model=MODEL,
    model_settings=SETTINGS,
    output_type=AgentOutputSchema(Plan, strict_json_schema=False),
)


def _as_plan(output) -> Plan:
    if isinstance(output, Plan):
        return output
    match = re.search(r"\{.*\}", str(output), re.S)  # structured output failed: read JSON from the text
    try:
        return Plan(**json.loads(match.group(0))) if match else Plan()
    except (ValueError, TypeError):
        return Plan()


async def make_plan(question: str, state: Research) -> Plan:
    """The Lead's first call. Also run on its own by evals/run_evals.py plan."""
    lead_input = question
    if state.history:
        earlier = "\n".join(f"- {h['question']}" for h in state.history)
        have = "\n".join(f"[{f['id']}] ({f.get('period_covered') or 'period n/a'}) {f['claim']}" for f in state.findings)
        lead_input = (f"Earlier questions in this conversation:\n{earlier}\n\nVerified findings already gathered:\n"
                      f"{have or '(none)'}\n\nFollow-up question: {question}")
    run = await Runner.run(lead, lead_input, context=state, hooks=state.hooks, max_turns=2)
    _record_usage(state, lead.name, run)
    plan = _as_plan(run.final_output)
    state.plans.append({"round": 1, **plan.model_dump()})
    return plan


async def review(question: str, sent: dict[str, str], state: Research, round_no: int) -> Plan:
    """The Lead's look at what came back: follow-up tasks for the gaps, or none. Also run on its own
    by evals/run_evals.py plan (cases with findings)."""
    findings = "\n".join(f"[{f['id']}] ({f.get('period_covered') or 'period n/a'}) {f['claim']}" for f in state.findings)
    rejected = "\n".join(f"- {r['claim']} (rejected: {r['reason']})" for r in state.rejected[-8:])
    tasks = "\n".join(f"{kind}: {task}" for kind, task in sent.items())
    message = (f"Question: {question}\n\nTasks sent:\n{tasks}\n\nVerified findings:\n{findings or '(none)'}\n\n"
               f"Gaps the researchers reported:\n" + ("\n".join(f"- {g}" for g in state.gaps) or "(none)")
               + f"\n\nRejected by the evidence check:\n{rejected or '(none)'}")
    run = await Runner.run(lead_review, message, context=state, hooks=state.hooks, max_turns=2)
    _record_usage(state, lead.name, run)
    plan = _as_plan(run.final_output)
    plan.clarify = None  # reviewing, not asking the user
    if not plan.unanswered:
        # No part of the question named as unanswered: no second round, whatever tasks came with it.
        # The review once sent a whole round for coach pay and OCR tolerance rules on a compliance
        # question the first round had answered (30 calls, 207k tokens).
        plan.data_task = plan.policy_task = plan.news_task = None
    state.plans.append({"round": round_no, **plan.model_dump()})
    return plan


def plan_tasks(plan: Plan) -> dict[str, str]:
    """{kind: task} for every researcher the plan sends, in a fixed order."""
    tasks = {"data": plan.data_task, "policy": plan.policy_task, "news": plan.news_task}
    return {kind: t.strip() for kind, t in tasks.items() if t and t.strip()}


# --- The writer: one call, after the research ends ---

writer = Agent(
    name="Writer",
    instructions="""\
Write a short answer in markdown, using only the verified findings you're given. Each finding has
an id (F3), a source type, an access level and the period it covers.

Length: about 250 words; never more than 400. A reader should get the answer in the first lines.
If earlier_in_this_conversation is given, this is a follow-up: answer the new question directly,
building on those answers without repeating them, and shorter if the question is narrow.
- Open with the direct answer in 2-3 sentences, with the key numbers.
- Then only what the question asked about: a few bullets, or at most 3 short ### sections. Leave out
  findings that don't help answer it: background rules and definitions only when the question asks,
  or in one sentence when they change how to read a number. Don't restate the question or describe
  how the research was done.
- EADA runs from 2015-16 through 2024-25. If the findings stop earlier, say the findings do, never
  that the data does. With no findings for part of the question, say the research didn't produce a
  verified answer for it; don't guess why.
- When the answer compares 3 or more numbers (a trend over years, or groups side by side), show
  them as a chart rather than a table. Write it as a fenced block, ```chart, holding JSON:
    {"type": "line" (over time) or "bar" (groups), "title": "<what, which period>",
     "unit": "$" or "%" or "count", "labels": ["2021-22", "2022-23", ...],
     "series": [{"name": "Revenue", "values": [859753293, 1055280440, ...]}],
     "sources": ["F4", "F5", ...]}
  Copy each value exactly from a finding (no converting to millions: the page formats them), one
  per label (null if missing), at most 3 series, one unit per chart. List every finding the values
  come from in sources. Values not in those findings get the chart removed. One chart at most;
  then no table of the same numbers, and in the text give only the 1-2 numbers that matter most.
- Otherwise one table at most, only to compare 3 or more numbers; give it a column for the period,
  and don't repeat its numbers in the text.
- End every sentence that states a fact with its finding ids, one per bracket: [F3] or [F3][F7].
  Use only the ids you're given.
- EADA words: "total expenses" (expense_*) vs. "game-day operating expenses" (opexp_*, a part of
  them). Never call total expenses "operating expenses".
- Label every number with the period it covers ("in 2023-24", "projected for 2025-26"), and say
  what group it covers ("Division I schools", "Big Ten schools") when the finding says.
- Weigh sources: official filings and official bodies, then policy documents, then industry reports,
  then news, then estimates. Attribute anything below official: "Opendorse projects...", "according
  to <outlet>...". A finding with access 'snippet' or 'partial' rests on text that couldn't be read
  in full: mark each sentence that uses one with "(from a search snippet)" or "(partial page)".
- Keep the kinds of money apart, and never compare one with another as if they were the same thing:
  1. a school's athletics spending (EADA);
  2. revenue sharing: what schools pay current athletes from 2025-26 under the House settlement cap;
  3. third-party NIL: brands and collectives paying athletes;
  4. House settlement back-pay damages: $2.576 billion in total to former athletes, paid in yearly
     installments over ten years (often reported as "$2.8 billion"; its football / men's
     basketball / women's basketball split is not revenue sharing). Its $1.976 billion NIL claims
     fund and $600 million additional compensation fund are parts of that total: never call either
     one the total.
  Open the answer with the kind of money the question asks about. If two kinds appear together,
  say in one sentence that they measure different things.
- End with "### Limits": at most 3 bullets, only the ones that change how to read this answer (a
  gap, a rejected area, EADA lagging real time, proportionality being one prong of Title IX's
  three-part test and not by itself showing compliance).
- Don't write a Sources section: it's added after you.
- Plain, specific sentences. No "pivotal", "landscape", "it is worth noting".""",
    model=MODEL,
    model_settings=SETTINGS,
)


# --- Citations: per-source numbers, the Sources list, and the page's citation cards ---

# One or more adjacent citations: [F3][F7], and the Writer's occasional [F3, F7]
_FINDING_REFS = re.compile(r"(?:\[F\d+(?:\s*,\s*F\d+)*\])+")
_CHART_SOURCES = re.compile(r'"sources":\[([^\]]*)\]')


def number_sources(markdown: str, findings: list[dict]) -> tuple[str, dict[str, int]]:
    """Readers get one number per source, in order of first citation, with no gaps: five findings
    from one query are all [1]. Finding ids stay internal (evidence checks, the Writer, the run log).
    Returns the renumbered markdown and {source_url: number}."""
    by_id = {f["id"]: f for f in findings}
    numbers: dict[str, int] = {}

    def number(fid: str) -> int | None:
        f = by_id.get(fid)
        if f is None:
            return None
        url = f.get("source_url", "")
        if url not in numbers:
            numbers[url] = len(numbers) + 1
        return numbers[url]

    # Number in reading order: text citations and chart sources share one sequence
    order = re.compile(_FINDING_REFS.pattern + "|" + _CHART_SOURCES.pattern)
    for m in order.finditer(markdown):
        for fid in re.findall(r"F\d+", m.group(0)):
            number(fid)

    def cite(m: re.Match) -> str:
        nums = dict.fromkeys(n for fid in re.findall(r"F\d+", m.group(0)) if (n := number(fid)))
        return "".join(f"[{n}]" for n in nums) or m.group(0)  # an unknown id stays visible

    def chart_sources(m: re.Match) -> str:
        nums = dict.fromkeys(n for fid in re.findall(r"F\d+", m.group(1)) if (n := number(fid)))
        return '"sources":[' + ",".join(f'"{n}"' for n in nums) + "]"

    markdown = _FINDING_REFS.sub(cite, markdown)
    return _CHART_SOURCES.sub(chart_sources, markdown), numbers


def _escape_sql(query: str) -> str:
    # The query goes into raw HTML inside markdown: escape HTML, and the characters markdown would
    # read as emphasis (COUNT(*) ... COUNT(*) turns into italics otherwise).
    return html.escape(query.strip()).replace("*", "&#42;").replace("_", "&#95;").replace("\n", "&#10;")


def _sql_span(result: dict) -> str:
    columns = result.get("columns", [])
    col = columns.index("survey_year") if "survey_year" in columns else None
    years = sorted({row[col] for row in result.get("rows", []) if col is not None and isinstance(row[col], int)})
    if len(years) > 1 and years == list(range(years[0], years[-1] + 1)):
        return f"survey years {years[0]}-{years[-1]}"
    return f"survey year{'s' if len(years) > 1 else ''} {', '.join(map(str, years))}" if years else ""  # a gap: list them


def source_cards(numbers: dict[str, int], cited_ids: set[str], state: Research) -> list[dict]:
    """One card per cited source, in number order: what it is, and the claims from it this report
    cites with the exact evidence each rests on. The page shows these when a reader hovers [1]."""
    cards = []
    for url, n in sorted(numbers.items(), key=lambda kv: kv[1]):
        uses = [f for f in state.findings if f.get("source_url") == url and f["id"] in cited_ids]
        if not uses:
            continue
        first = uses[0]
        card = {"n": n, "source_type": first.get("source_type"), "access": first.get("access"),
                "published": first.get("published"),
                "claims": [{"claim": f["claim"], "evidence": f.get("evidence_text"), "period": f.get("period_covered")} for f in uses]}
        if url.startswith("sql:"):
            result = state.sql_results.get(url[4:], {})
            query = result.get("query", "")
            if "nil_facts" in query.lower() and "institutions" not in query.lower() and "sports" not in query.lower():
                # The curated figures table: its rows come from reports and the court record, not EADA
                card.update(kind="curated", title="Curated NIL and settlement figures (data/nil_facts.csv)", sql=query)
            else:
                span = _sql_span(result)
                card.update(kind="sql", title=f"EADA database query{', ' + span if span else ''}", sql=query)
        else:
            page = state.pages.get(url, {})
            card.update(kind="policy" if url.startswith("policy:") else "web",
                        title=page.get("title") or url, url=page.get("document_url") or url)
        cards.append(card)
    return cards


def sources_section(cards: list[dict]) -> str:
    """The Sources list in the markdown (for /chat and copying); the page adds hover cards on top."""
    lines = ["\n\n## Sources\n"]
    for c in cards:
        if c["kind"] in ("sql", "curated"):
            label = f"{c['title']} <details><summary>SQL</summary><pre><code>{_escape_sql(c['sql'])}</code></pre></details>"
        else:
            date_note = f"published {c['published']}" if c.get("published") else "publication date unknown"
            label = f"[{c['title']}]({c['url']}): {c['source_type']}, {date_note}, {c['access']} access"
        lines.append(f"- **[{c['n']}]** {label}")
    return "\n".join(lines)


# --- A question, start to finish ---


async def deep_research(question: str, hooks: ResearchHooks, prior: Research | None = None) -> dict:
    """Run the team on one question. Returns {type: 'clarify' | 'report', markdown, state, ...}.
    prior: the state an earlier question in the same conversation left (result['state']), for a
    follow-up that reuses its findings. Every run, finished or not, leaves a trace in
    data/cache/runs/ (see runlog.py)."""
    state = prior.follow_up(hooks) if prior else Research(hooks=hooks)
    started = time.time()
    result: dict = {"type": "error", "markdown": ""}
    try:
        result = await _deep_research(question, state, hooks)
        return result
    except Exception as e:
        result = {"type": "error", "markdown": f"{type(e).__name__}: {str(e)[:500]}"}
        raise
    finally:
        runlog.save(question, state, result, started)


async def _deep_research(question: str, state: Research, hooks: ResearchHooks) -> dict:
    # 1. Plan: clarify, or a task for each researcher the question needs
    plan = await make_plan(question, state)
    tasks = plan_tasks(plan)
    if plan.clarify and not tasks:
        return {"type": "clarify", "markdown": plan.clarify.strip(), "tool_calls": hooks.tool_calls, "state": state}

    # 2. Research in rounds: the Lead fans out to its researchers at once (in parallel, fresh
    #    contexts), each one's findings verified as it finishes; then the Lead reviews what came back
    #    and goes back for what's missing, up to MAX_ROUNDS.
    for round_no in range(1, MAX_ROUNDS + 1):
        await asyncio.gather(*(run_researcher(kind, task, state, round_no) for kind, task in tasks.items()))
        if round_no == MAX_ROUNDS:
            break
        tasks = plan_tasks(await review(question, tasks, state, round_no + 1))
        if not tasks:
            break

    # 3. Write: one call. The Writer sees the question and the verified findings only.
    keys = ("id", "claim", "evidence_text", "source_type", "access", "published", "period_covered")
    brief = {
        "question": question,
        "verified_findings": [{k: f.get(k) for k in keys} for f in state.findings],
        "gaps": state.gaps,
        "rejected_claims_do_not_state_as_fact": [r["claim"] for r in state.rejected],
    }
    if state.history:
        # A follow-up: answer the new question; the earlier answers are context, not to be repeated
        brief["earlier_in_this_conversation"] = state.history[-3:]
    report = await Runner.run(writer, json.dumps(brief, indent=1, default=str), hooks=hooks)
    _record_usage(state, writer.name, report)
    markdown = str(report.final_output).strip()

    # 4. Check: every chart value must come from verified findings, every [F#] must be one
    markdown, charts_dropped = evidence.check_charts(markdown, state.findings)
    ids = {f["id"] for f in state.findings}
    check = evidence.check_citations(markdown, ids)
    check["charts_dropped"] = charts_dropped
    # A chart's sources count as citations: its findings belong in the Sources section
    for chart_ids in _CHART_SOURCES.findall(markdown):
        cited_by_chart = set(re.findall(r"F\d+", chart_ids))
        check["uncited"] = [i for i in check["uncited"] if i not in cited_by_chart]
    if check["unknown"]:
        markdown += f"\n\n> Citation check: {', '.join(check['unknown'])} don't match any verified finding."
    cited_ids = ids - set(check["uncited"])
    markdown, numbers = number_sources(markdown, state.findings)
    cards = source_cards(numbers, cited_ids, state)
    state.history.append({"question": question, "answer": re.sub(r"```chart.*?```", "", markdown, flags=re.S)[:1500]})
    markdown += sources_section(cards)
    return {
        "type": "report",
        "markdown": markdown,
        "sources": cards,
        "state": state,
        "findings": state.findings,
        "rejected": state.rejected,
        "citation_check": check,
        "tool_calls": hooks.tool_calls,
    }
