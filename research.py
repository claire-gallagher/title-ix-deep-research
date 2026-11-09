"""The research team: a Lead that plans and fans out, three kinds of Researcher, and a Writer.

Built from wikipedia-deep-research's pieces. What's different:
- Three Researcher kinds with different tools: data (SQL over EADA + curated NIL figures),
  news (web search and fetch), policy (primary legal documents).
- Researchers return structured findings, each with the evidence span it rests on. research()
  checks every one with evidence.verify() before the Lead or Writer sees it.
- The Lead can stop and ask a clarifying question instead of researching.
- The Writer cites finding ids ([F3]); the Sources section is built in code from the findings,
  so a citation can only point at something this run actually retrieved.
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date

from agents import Agent, AgentOutputSchema, ModelSettings, RunContextWrapper, Runner, function_tool
from agents.extensions.models.litellm_model import LitellmModel
from pydantic import BaseModel

import eada
import evidence
import policy
import web
from context_panel import PanelHooks

# LiteLLM routes the call to Gemini. api_key is unused: Vertex AI uses your gcloud credentials.
MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash", api_key="unused")
# Researchers read and take notes: the cheaper model is enough.
RESEARCHER_MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash-lite", api_key="unused")
# A stalled model call would hang the page: give up after 90 s, and retry twice.
SETTINGS = ModelSettings(extra_args={"vertex_location": "global", "timeout": 90, "num_retries": 2})

# The Lead gets this many researchers in all. Prompts ask for restraint; code enforces it.
MAX_RESEARCHERS = 6
# Web searches per run, across all News Researchers: each one spends a Tavily credit.
MAX_SEARCHES = 8
CLARIFY = "CLARIFY:"


# --- Shared state ---


class ResearchHooks(PanelHooks):
    """The template's PanelHooks, plus a record of every tool call for /chat's tool_calls.
    Each call carries the agent that made it, so sub-agents' calls are distinguishable."""

    def __init__(self, contexts, emit):
        super().__init__(contexts, emit)
        self.tool_calls: list[dict] = []

    async def on_tool_end(self, context, agent, tool, result):
        raw = getattr(context, "tool_arguments", None)
        try:
            args = json.loads(raw) if raw else {}
        except ValueError:
            args = raw
        self.tool_calls.append({"agent": agent.name, "name": tool.name, "args": args, "result": str(result)[:4000]})


@dataclass
class Research:
    """One run's state, shared by every agent and tool in it. The model never sees it."""

    hooks: ResearchHooks
    researchers: int = 0
    queries: int = 0
    searches: int = 0
    findings: list[dict] = field(default_factory=list)  # verified only, ids F1..Fn, for the Writer
    rejected: list[dict] = field(default_factory=list)  # failed evidence checks, with the reason
    gaps: list[str] = field(default_factory=list)
    # What this run actually retrieved: evidence.verify() checks findings against these
    pages: dict[str, dict] = field(default_factory=dict)  # url or 'policy:<chunk>' -> {text, access, ...}
    sql_results: dict[str, dict] = field(default_factory=dict)  # 'q3' -> {query, columns, rows}


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


def _error(e: Exception) -> str:
    # The model cannot see an exception. Return something it can reason about.
    return json.dumps({"error": f"{type(e).__name__}: {str(e)[:300]}"})


# --- Data tools (EADA + nil_facts) ---


@function_tool
def read_mappings() -> str:
    """Read the database's tables and columns (name, type, description, is_enum) and the known gotchas.
    Call this first."""
    try:
        return json.dumps(eada.read_mappings(), default=str)
    except Exception as e:
        return _error(e)


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
def run_sql(ctx: RunContextWrapper[Research], query: str) -> str:
    """Run a read-only DuckDB SQL query. Returns {query_id, columns, rows, truncated}.
    Cite a result in a finding as source_url 'sql:<query_id>'.

    Args:
        query: A DuckDB SQL query.
    """
    try:
        result = eada.run_sql(query)
    except Exception as e:
        return _error(e)
    if "error" in result:
        return json.dumps(result)
    ctx.context.queries += 1
    query_id = f"q{ctx.context.queries}"
    ctx.context.sql_results[query_id] = {"query": query, **result}
    return json.dumps({"query_id": query_id, **result}, default=str)


# --- News tools ---


@function_tool
def search_web(ctx: RunContextWrapper[Research], query: str) -> str:
    """Search the web for recent news and reports. Returns titles, URLs, snippets, dates and each
    domain's source type. Snippets are not articles: fetch a page before relying on it.

    Args:
        query: A short search query, 2-8 words.
    """
    if ctx.context.searches >= MAX_SEARCHES:
        return json.dumps({"error": f"Search limit reached ({MAX_SEARCHES} per question). Work with the pages you have: fetch the most promising results, or report what you found."})
    ctx.context.searches += 1
    try:
        results = web.search(query)
    except Exception as e:
        return _error(e)
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


# --- Policy tools ---


@function_tool
def search_policy(query: str) -> str:
    """Search primary policy documents (Title IX statute and regulations, the three-part test,
    House v. NCAA settlement terms, NIL Go rules). Returns matching chunks with ids and snippets.

    Args:
        query: What to look for, e.g. 'three-part test substantially proportionate'.
    """
    return json.dumps(policy.search(query))


@function_tool
def read_policy(ctx: RunContextWrapper[Research], chunk_id: str) -> str:
    """Read one policy chunk in full. Cite it as source_url 'policy:<chunk_id>'.

    Args:
        chunk_id: A chunk_id from search_policy, e.g. '34-cfr-106-41#2'.
    """
    chunk = policy.read(chunk_id)
    if chunk is None:
        return json.dumps({"error": f"No chunk '{chunk_id}'. Use a chunk_id from search_policy."})
    ctx.context.pages[f"policy:{chunk_id}"] = {
        "text": chunk["text"], "access": "full", "published": chunk.get("published"),
        "title": chunk.get("title"), "source_type": "policy_document", "document_url": chunk.get("source_url"),
    }
    return json.dumps({"source_url": f"policy:{chunk_id}", **{k: chunk.get(k) for k in ("title", "source_url", "published", "text")}})


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
At most 8 findings. Findings are checked in code against what the tools returned; ones that don't
match are thrown away. In gaps, list the parts of the task the sources didn't answer."""

data_researcher = Agent(
    name="Data Researcher",
    instructions=f"""\
You answer one research task with SQL over a DuckDB database:
- EADA (U.S. Equity in Athletics Disclosure Act) data: participation, enrollment, coaching, spending
  and revenue by school, sport, gender and year. Official filings (source_type official_filing).
- nil_facts: curated NIL and revenue-sharing figures from reports, one row per figure, with its
  source, period, method note and URL. Use each row's own source_type.

1. Call read_mappings first. Read the gotchas: they describe mistakes that give wrong answers.
2. Resolve school names through institution_aliases (ILIKE), then filter on unitid.
3. Before filtering on an enum column, call get_enum_values. Never guess values.
4. Prefer the derived views (v_proportionality, v_spending_by_gender) to re-deriving metrics.
5. Always SELECT survey_year (EADA) or period_covered and source_org (nil_facts), so the evidence
   carries the period. For nil_facts, keep the method_note: it says what the number measures.
6. Run at most 8 queries. If the data can't answer part of the task, say so in gaps: EADA has no
   NIL deals, and its latest year predates revenue sharing.

{FINDINGS_RULES}
For SQL findings, source_url is 'sql:<query_id>' and evidence_text lists the values, e.g.
"survey_year=2023, institution_name=University of X, female_athlete_pct=47.2".""",
    model=RESEARCHER_MODEL,
    model_settings=SETTINGS,
    tools=[read_mappings, get_enum_values, run_sql],
    output_type=AgentOutputSchema(Findings, strict_json_schema=False),
)

news_researcher = Agent(
    name="News Researcher",
    instructions=f"""\
You answer one research task with recent news and reports from the web.

1. Search with search_web (at most 3 searches). Prefer official bodies, the organisations that
   published a report, and open news sites to paywalled ones.
2. Fetch the most relevant pages with fetch_page (at most 4). Only what fetch_page or search_web
   returned can be evidence.
3. Follow stories to primary sources: when an article reports on a report, filing, press release
   or court document, find and fetch that, and cite it instead of the article.
4. If a page is 'partial' or 'blocked' (paywall, bot check), don't guess what it says. Use another
   source, or the search snippet with access 'snippet'. Never use archive sites or paywall workarounds.
5. Use the publication date fetch_page reports. If it's null, published is null: never take the
   date printed in a site's header, which is often today's date.
6. Opendorse, On3 and similar companies sell NIL services: their figures are industry_report or
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
You answer one research task from primary policy documents: Title IX and its athletics regulations,
the three-part test, Department of Education guidance, House v. NCAA settlement terms, NIL Go rules.

1. Search with search_policy (at most 3 searches), then read the best chunks with read_policy.
2. Quote the rule itself. Don't interpret beyond what the text says; note where it's ambiguous.

{FINDINGS_RULES}
source_type is policy_document; source_url is 'policy:<chunk_id>'.""",
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


@function_tool
async def research(ctx: RunContextWrapper[Research], kind: str, task: str) -> str:
    """Send a researcher to answer one task. Returns its verified findings (with ids), the findings
    that failed evidence checks, and what it couldn't find.

    Args:
        kind: 'data' (EADA statistics and curated NIL figures), 'news' (recent web sources), or
            'policy' (primary legal and policy documents).
        task: One specific thing to find out, with the schools, years and measures it concerns.
    """
    if kind not in RESEARCHERS:
        return json.dumps({"error": f"kind must be one of {sorted(RESEARCHERS)}"})
    if kind == "news" and not os.environ.get("TAVILY_API_KEY"):
        return json.dumps({"error": "News research is unavailable (no search API key). Use data or policy."})
    if ctx.context.researchers >= MAX_RESEARCHERS:
        return "No researchers left. Reply DONE."

    # What researcher.as_tool() does: run the researcher in a fresh context and return its answer.
    # Written out so each run gets a numbered copy, its own column in the context panel, and its
    # findings verified before anyone reads them.
    ctx.context.researchers += 1
    agent = RESEARCHERS[kind].clone(name=f"{RESEARCHERS[kind].name} {ctx.context.researchers}")
    try:
        result = await Runner.run(agent, f"Task: {task}", context=ctx.context, hooks=ctx.context.hooks, max_turns=16)
    except Exception as e:
        return _error(e)
    findings, gaps = _parse(result.final_output)

    accepted, rejected = [], []
    for finding in findings:
        ok, reason = evidence.verify(finding, ctx.context.pages, ctx.context.sql_results)
        if ok:
            finding.update(id=f"F{len(ctx.context.findings) + 1}", task=task, researcher=agent.name, check=reason)
            ctx.context.findings.append(finding)
            accepted.append(finding)
        else:
            rejected.append({"claim": finding.get("claim"), "source_url": finding.get("source_url"), "reason": reason})
    ctx.context.rejected += rejected
    ctx.context.gaps += gaps
    hooks = ctx.context.hooks
    hooks.activity(agent.name, f"{len(accepted)} findings verified, {len(rejected)} rejected")

    keys = ("id", "claim", "source_type", "access", "period_covered")
    return json.dumps({"verified_findings": [{k: f.get(k) for k in keys} for f in accepted], "rejected": rejected, "gaps": gaps})


# --- The lead ---

LEAD_PROMPT = """\
You lead a research team answering questions about gender equity in college athletics: participation,
spending and revenue by school and gender, and how revenue sharing and NIL are changing them.
Today is {today}.

Your researchers (call research with a kind and a task):
- data: SQL over EADA, the federal Equity in Athletics data (participation, enrollment, spending,
  revenue by school, sport, gender and year; it lags real time by a year or two), plus a curated
  table of NIL and revenue-sharing figures from reports. All numbers about schools come from here.
- news: web search, for what happened after the data or what it doesn't cover (schools'
  revenue-sharing splits, lawsuits, investigations, conference decisions). Slower; less reliable.
- policy: primary legal documents (Title IX regulations, the three-part test, House v. NCAA
  settlement terms, NIL Go rules).

1. Is the question specific enough to research? If it needs a school, conference, sport, year range
   or measure and doesn't say, or is too broad to answer well, reply with exactly one line:
   CLARIFY: <one short question, offering 2-3 concrete options>
   and don't call research. Ask once at most: if the message already includes the user's answer to
   a clarifying question, go ahead, choosing sensible defaults where still needed.
2. Split the question into 2 to 4 independent tasks. Call research once per task, all in the same
   turn, so the researchers work in parallel.
3. Read the results. Rejected findings failed evidence checks: don't rely on them. If an important
   part is still unanswered, call research again for just that part, at most twice more.
   Some things no source can answer, like whether a school is legally compliant with Title IX or
   what an individual athlete earns: don't keep searching for those; the writer will say so.
4. When the question is covered, reply DONE and nothing else: a writer writes the report."""


def lead_instructions(ctx, agent) -> str:
    return LEAD_PROMPT.replace("{today}", date.today().isoformat())


lead = Agent(
    name="Lead",
    instructions=lead_instructions,
    model=MODEL,
    model_settings=SETTINGS,
    tools=[research],
)

# --- The writer: one call, after the research ends ---

writer = Agent(
    name="Writer",
    instructions="""\
Write a report in markdown that answers the question, using only the verified findings you're given.
Each finding has an id (F3), a source type, an access level and the period it covers.

- Start with a 2-4 sentence answer. Then a section per part of the question. When you compare
  numbers, include a markdown table with a column for the period.
- End every sentence that states a fact with its finding id in brackets, e.g. [F3]. Use only the
  ids you're given.
- Label every number with the period it covers ("in 2023-24", "projected for 2025-26").
- Weigh sources: official filings and official bodies, then policy documents, then industry reports,
  then news, then estimates. Attribute anything below official: "Opendorse projects...", "according
  to <outlet>...". Findings with access 'snippet' or 'partial' could not be fully verified: say so.
- Keep three kinds of money apart: a school's athletics spending (EADA), revenue sharing (school to
  athlete), and third-party NIL (brands and collectives to athlete). If you put them side by side,
  say they measure different things.
- End with a section "What the data can't tell us": the gaps and rejected areas you're given, and
  limits that apply (EADA lags real time; proportionality is one prong of Title IX's three-part
  test and doesn't by itself show compliance or non-compliance).
- Don't write a Sources section: it's added after you.
- Plain, specific sentences. No "pivotal", "landscape", "it is worth noting".""",
    model=MODEL,
    model_settings=SETTINGS,
)


def sources_section(findings: list[dict], state: Research) -> str:
    """Built in code from what was retrieved, so every citation resolves to a real source."""
    lines = ["\n\n## Sources\n"]
    for f in findings:
        url = f.get("source_url", "")
        if url.startswith("sql:"):
            query = state.sql_results.get(url[4:], {}).get("query", "")
            label = f"EADA database query {url[4:]}: `{' '.join(query.split())[:200]}`"
        else:
            page = state.pages.get(url, {})
            link = page.get("document_url") or url
            title = page.get("title") or link
            date_note = f"published {f['published']}" if f.get("published") else "publication date unknown"
            label = f"[{title}]({link}): {f.get('source_type')}, {date_note}, {f.get('access')} access"
        lines.append(f"- **[{f['id']}]** {label}")
    return "\n".join(lines)


async def deep_research(question: str, hooks: ResearchHooks) -> dict:
    """Run the team on one question. Returns {type: 'clarify' | 'report', markdown, ...}."""
    state = Research(hooks=hooks)

    # 1. Research: clarify or plan, fan out, check for gaps
    lead_run = await Runner.run(lead, question, context=state, hooks=hooks, max_turns=12)
    lead_output = str(lead_run.final_output).strip()
    if lead_output.upper().startswith(CLARIFY) and not state.findings:
        return {"type": "clarify", "markdown": lead_output[len(CLARIFY):].strip(), "tool_calls": hooks.tool_calls}

    # 2. Write: one call, outside the loop. The Writer sees the question and the verified findings only.
    keys = ("id", "claim", "evidence_text", "source_type", "access", "published", "period_covered")
    brief = {
        "question": question,
        "verified_findings": [{k: f.get(k) for k in keys} for f in state.findings],
        "gaps": state.gaps,
        "rejected_claims_do_not_state_as_fact": [r["claim"] for r in state.rejected],
    }
    report = await Runner.run(writer, json.dumps(brief, indent=1, default=str), hooks=hooks)
    markdown = str(report.final_output).strip()

    # 3. Check: every [F#] must be a verified finding
    ids = {f["id"] for f in state.findings}
    check = evidence.check_citations(markdown, ids)
    if check["unknown"]:
        markdown += f"\n\n> Citation check: {', '.join(check['unknown'])} don't match any verified finding."
    cited = [f for f in state.findings if f["id"] not in check["uncited"]]
    markdown += sources_section(cited, state)
    return {
        "type": "report",
        "markdown": markdown,
        "findings": state.findings,
        "rejected": state.rejected,
        "citation_check": check,
        "tool_calls": hooks.tool_calls,
    }
