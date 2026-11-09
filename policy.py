"""Search a fixed corpus of primary policy documents: data/policy/*.md.

Each file is one document, saved as Markdown with a header block:

    title: Title IX athletics regulation (34 CFR 106.41)
    source_url: https://www.ecfr.gov/...
    published: 2020-05-19
    ---
    <the document text>

A fixed corpus gives the Policy Researcher clean, checkable citations, and keeps claims about the
law out of the noisier news layer. BM25 over paragraph chunks is enough at this size: a few dozen
documents. Stdlib only.

  uv run policy.py "revenue sharing cap"
"""

import math
import re
from collections import Counter
from pathlib import Path

POLICY_DIR = Path(__file__).parent / "data" / "policy"
CHUNK_CHARS = 1_500

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("the a an of to and or in on for by with is are be as at that this from it its was were".split())


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def _load() -> list[dict]:
    """Every document, split into chunks of whole paragraphs."""
    chunks = []
    for path in sorted(POLICY_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        raw = path.read_text()
        header, _, body = raw.partition("\n---\n")
        meta = dict(line.split(":", 1) for line in header.splitlines() if ":" in line)
        meta = {k.strip(): v.strip() for k, v in meta.items()}
        current, n = "", 0
        for paragraph in re.split(r"\n\s*\n", body):
            if current and len(current) + len(paragraph) > CHUNK_CHARS:
                chunks.append({"chunk_id": f"{path.stem}#{n}", "doc_id": path.stem, "text": current.strip(), **meta})
                current, n = "", n + 1
            current += paragraph + "\n\n"
        if current.strip():
            chunks.append({"chunk_id": f"{path.stem}#{n}", "doc_id": path.stem, "text": current.strip(), **meta})
    return chunks


CHUNKS = _load()
_TF = [Counter(_tokens(c["text"])) for c in CHUNKS]
_DF = Counter(word for tf in _TF for word in tf)
_AVG = sum(sum(tf.values()) for tf in _TF) / max(len(_TF), 1)


def search(query: str, k: int = 5) -> list[dict]:
    """The k best chunks for a query by BM25: [{chunk_id, title, source_url, published, snippet}]."""
    n, k1, b = len(CHUNKS), 1.5, 0.75
    scores = []
    for i, tf in enumerate(_TF):
        length = sum(tf.values())
        score = 0.0
        for word in set(_tokens(query)):
            if word in tf:
                idf = math.log(1 + (n - _DF[word] + 0.5) / (_DF[word] + 0.5))
                score += idf * tf[word] * (k1 + 1) / (tf[word] + k1 * (1 - b + b * length / _AVG))
        if score > 0:
            scores.append((score, i))
    return [
        {key: CHUNKS[i].get(key) for key in ("chunk_id", "title", "source_url", "published")}
        | {"snippet": CHUNKS[i]["text"][:300]}
        for _, i in sorted(scores, reverse=True)[:k]
    ]


def read(chunk_id: str) -> dict | None:
    """One chunk in full, with its document's metadata."""
    return next((c for c in CHUNKS if c["chunk_id"] == chunk_id), None)


if __name__ == "__main__":
    import json
    import sys

    print(f"{len(CHUNKS)} chunks from {len({c['doc_id'] for c in CHUNKS})} documents")
    for hit in search(" ".join(sys.argv[1:]) or "three-part test effective accommodation"):
        print(json.dumps(hit, indent=2))
