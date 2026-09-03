"""Researcher node: Tavily search -> 10 cited sources, disk-cached.

Caching exists for two reasons: (1) eval re-runs are reproducible and free —
Tavily's free tier is 1,000 credits/month and debugging the editor loop would
burn it in a day otherwise; (2) the fact-check retry loop re-enters this node
and must see *new* sources, so the cache key includes the research round.
"""
from __future__ import annotations

import hashlib
import json
import time

from . import config
from .state import PipelineState, Source, Telemetry
from .tracker import telem


def _source_id(source: dict) -> str:
    """Return a stable provenance key independent of list position."""
    explicit = str(source.get("source_id") or "").strip()
    if explicit:
        return explicit
    identity = str(source.get("url") or source.get("title") or "").strip()
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _normalize_source(source: dict) -> Source:
    return Source(
        source_id=_source_id(source),
        title=str(source.get("title") or "")[:200],
        url=str(source.get("url") or ""),
        snippet=str(source.get("snippet") or "").strip(),
        date=str(source.get("date") or "")[:30],
    )


# ---------------------------------------------------------------- live ----
def _tavily_search(query: str) -> list[Source]:
    from tavily import TavilyClient
    client = TavilyClient(api_key=config.TAVILY_API_KEY)
    resp = client.search(query=query, max_results=config.N_SOURCES, search_depth="basic")
    out: list[Source] = []
    for r in resp.get("results", []):
        snippet = (r.get("content") or "").strip()
        if not snippet:
            continue
        out.append(_normalize_source({
            "title": (r.get("title") or "")[:200],
            "url": r.get("url") or "",
            "snippet": snippet,
            "date": (r.get("published_date") or "")[:30],
        }))
    return out


# ---------------------------------------------------------------- mock ----
def _mock_search(topic: str, rnd: int) -> list[Source]:
    data = json.loads(config.MOCK_SOURCES_PATH.read_text(encoding="utf-8"))
    t = topic.lower()
    matched: list[Source] = []
    for keyword, entries in data.get("topics", {}).items():
        if keyword in t:
            matched.extend(_normalize_source(e) for e in entries)
    matched_urls = {s["url"] for s in matched}
    pool = matched + [_normalize_source(e) for e in data.get("generic", [])
                      if e.get("url") not in matched_urls]
    window = pool[rnd * 5: rnd * 5 + 5]           # each retry round adds NEW sources
    return list(window)


# --------------------------------------------------------------- public ---
def fetch_sources(topic: str, rnd: int, use_cache: bool = True) -> tuple[list[Source], bool]:
    """Returns (sources, from_cache). Round 0 is the initial search; each fact-
    check retry asks for the next window of results. The cache key includes the
    mode so mock-corpus entries can never poison live Tavily results."""
    mode = "tavily" if config.TAVILY_API_KEY else "mock"
    key = hashlib.sha1(f"{topic}|{rnd}|{mode}".encode()).hexdigest()[:16]
    cache_file = config.CACHE_DIR / f"{key}.json"
    if use_cache and cache_file.exists():
        return [_normalize_source(s) for s in json.loads(cache_file.read_text(encoding="utf-8"))], True

    query = topic if rnd == 0 else f"{topic} latest facts official details"
    if config.TAVILY_API_KEY:
        try:
            sources = _tavily_search(query)
        except Exception:                    # quota, network, bad key -> mock
            sources = []
        if not sources:
            sources = _mock_search(topic, rnd)
    else:
        sources = _mock_search(topic, rnd)

    if use_cache:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps([dict(s) for s in sources], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    return sources, False


def research_node(state: PipelineState) -> dict:
    t0 = time.perf_counter()
    topic = state["topic"]
    rnd = state.get("research_round", 0)
    sources, from_cache = fetch_sources(topic, rnd)
    seen = {s["url"] for s in state.get("sources", [])}
    fresh = [s for s in sources if s["url"] and s["url"] not in seen]
    merged = state.get("sources", []) + fresh
    note = "cache hit" if from_cache else ("tavily" if config.TAVILY_API_KEY else "mock corpus")
    return {
        "sources": merged,
        "research_round": rnd + 1,
        "telemetry": [telem("researcher", t0, detail=f"{len(fresh)} new sources ({note})")],
        "log": [f"[researcher] round {rnd + 1}: +{len(fresh)} sources via {note} (total {len(merged)})"],
    }


def research_rounds_done(state: PipelineState) -> int:
    return state.get("research_round", 0)
