"""Web search and page fetching for the News Researcher.

fetch() returns what was actually readable, and says how much that was:
  access = "full"     the article text came through
  access = "partial"  a teaser, a login wall or a "subscribe to continue" box
  access = "blocked"  401/403, bot protection, or nothing extractable
It never tries to get around a paywall or a bot check. evidence.py only accepts findings whose
evidence is in the text returned here, so a paywall leads to "couldn't verify", not to a guess.

Dates come from the page's metadata (trafilatura), never from the site header: some sports sites
print today's date at the top of every page, including articles that are years old.

Every fetch is cached as JSON with its retrieval time: reproducible traces, and the stored text
evidence.py checks against.

  uv run web.py --selfcheck            access detection, no network
  uv run web.py https://example.com    fetch one page and print what came back
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).parent
CACHE_DIR = Path(os.environ.get("CACHE_DIR", HERE / "data" / "cache")) / "pages"
SOURCES = json.loads((HERE / "data" / "sources.json").read_text())

# Enough of an article to find its claims. A whole long page would crowd the researcher's context.
MAX_CHARS = 12_000
# Below this many words, a news page is almost always a teaser or a wall, not the article.
MIN_ARTICLE_WORDS = 250
USER_AGENT = "title-ix-research-agent/0.1 (course project)"

PAYWALL_PHRASES = [
    "subscribe to continue",
    "subscribe to read",
    "already a subscriber",
    "to continue reading",
    "sign in to read",
    "create a free account to continue",
    "this article is for subscribers",
    "become a member to read",
]
BOT_PHRASES = ["verify you are human", "checking your browser", "enable javascript and cookies", "access denied"]


# --- Source tiers ---


def source_info(url: str) -> dict:
    """{'source_type', 'access'} for a URL's domain from data/sources.json, or 'unknown'."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for domain, info in SOURCES["domains"].items():
        if host == domain or host.endswith("." + domain):
            return info
    return {"source_type": "unknown", "access": "untested"}


# --- Access detection (pure functions: tested without the network) ---


def _json_ld_not_free(html: str) -> bool:
    """True if the page's schema.org JSON-LD marks the article isAccessibleForFree: false."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        if re.search(r'"isAccessibleForFree"\s*:\s*("?)false\1', block, re.I):
            return True
    return False


def detect_access(status: int, html: str, text: str) -> tuple[str, str]:
    """Classify how much of a page we really got. Returns (access, reason)."""
    if status in (401, 402, 403, 429):
        return "blocked", f"HTTP {status}"
    lowered = (html[:20_000] + text).lower()
    if any(p in lowered for p in BOT_PHRASES) and len(text.split()) < MIN_ARTICLE_WORDS:
        return "blocked", "bot protection page"
    if not text.strip():
        return "blocked", "no extractable text"
    if _json_ld_not_free(html):
        return "partial", "page metadata marks it isAccessibleForFree: false"
    if any(p in lowered for p in PAYWALL_PHRASES):
        return "partial", "paywall prompt on the page"
    if len(text.split()) < MIN_ARTICLE_WORDS:
        return "partial", f"only {len(text.split())} words extracted"
    return "full", "article text extracted"


# --- Fetch ---


def _cache_path(url: str) -> Path:
    return CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")


def fetch(url: str, max_age_hours: float = 24) -> dict:
    """Fetch a page: {url, title, published, text, access, access_reason, words, retrieved_at, source_type}.

    text is truncated to MAX_CHARS for the model; full_text keeps everything for evidence checks.
    """
    path = _cache_path(url)
    if path.exists():
        cached = json.loads(path.read_text())
        if time.time() - cached.get("retrieved_ts", 0) < max_age_hours * 3600:
            return cached

    import httpx  # imported here so --selfcheck needs no third-party packages
    import trafilatura

    try:
        response = httpx.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=20)
        status, html = response.status_code, response.text
    except httpx.HTTPError as e:
        status, html = 0, ""
        error = f"{type(e).__name__}: {e}"
    else:
        error = None

    meta = {}
    if html:
        extracted = trafilatura.extract(html, output_format="json", with_metadata=True, url=url)
        meta = json.loads(extracted) if extracted else {}
    text = meta.get("text") or ""
    access, reason = detect_access(status, html, text) if not error else ("blocked", error)

    page = {
        "url": url,
        "title": meta.get("title"),
        "published": meta.get("date"),  # from page metadata; None if the page doesn't say
        "full_text": text,
        "text": text[:MAX_CHARS] + ("\n\n[Page truncated.]" if len(text) > MAX_CHARS else ""),
        "access": access,
        "access_reason": reason,
        "words": len(text.split()),
        "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "retrieved_ts": time.time(),
        **{k: v for k, v in source_info(url).items() if k == "source_type"},
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(page))
    return page


# --- Search ---


def search(query: str, max_results: int = 6) -> list[dict]:
    """Search the web. Returns [{title, url, snippet, published, source_type, typical_access}].

    Tavily by default (TAVILY_API_KEY). TODO: confirm the request/response shape against Tavily's
    current docs, or swap in Brave/Exa here; nothing else depends on which API this is.
    """
    import httpx

    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("Set TAVILY_API_KEY in .env to enable web search.")
    response = httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": query, "max_results": max_results, "search_depth": "basic", "topic": "news"},
        timeout=30,
    )
    response.raise_for_status()
    results = []
    for r in response.json().get("results", []):
        info = source_info(r.get("url", ""))
        results.append({
            "title": r.get("title"),
            "url": r.get("url"),
            "snippet": r.get("content", "")[:600],
            "published": r.get("published_date"),
            "source_type": info["source_type"],
            "typical_access": info["access"],
        })
    # Known open, high-tier sources first: they can actually be verified
    rank = {"official_body": 0, "policy_document": 1, "official_filing": 1, "industry_report": 2, "news": 3, "legal_analysis": 3, "unknown": 4, "estimate": 5}
    results.sort(key=lambda r: (rank.get(r["source_type"], 4), r["typical_access"] == "paywalled"))
    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] != "--selfcheck":
        page = fetch(sys.argv[1], max_age_hours=0)
        print({k: v for k, v in page.items() if k not in ("text", "full_text")})
        print(page["text"][:800])
        sys.exit()

    article = " ".join(["word"] * 400)
    assert detect_access(200, "<html></html>", article)[0] == "full"
    assert detect_access(403, "", "")[0] == "blocked"
    assert detect_access(200, "<p>Checking your browser before accessing</p>", "Checking your browser")[0] == "blocked"
    assert detect_access(200, "", "Only a teaser paragraph here.")[0] == "partial"
    assert detect_access(200, "<div>Subscribe to continue reading</div>", article)[0] == "partial"
    ld = '<script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":"False"}</script>'
    assert detect_access(200, ld, article)[0] == "partial"
    assert source_info("https://www.apnews.com/article/x")["source_type"] == "news"
    assert source_info("https://nowhere.example/x")["source_type"] == "unknown"
    print(json.dumps({"web self-check": "ok"}))
