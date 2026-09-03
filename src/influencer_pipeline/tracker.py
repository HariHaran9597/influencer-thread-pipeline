"""Telemetry helpers + the final tracker node.

Every node stamps {node, ms, tokens, detail} into state; the tracker node
aggregates one run into reports/traces.jsonl — the "state preserved" artifact:
research bundle, draft versions, fact scores, editor critiques, guardrail
report, latency and token totals.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from . import config
from .state import PipelineState, Telemetry


def telem(node: str, t0: float, tokens: int = 0, detail: str = "") -> Telemetry:
    return Telemetry(node=node, ms=round((time.perf_counter() - t0) * 1000, 1),
                     tokens=tokens, detail=detail)


def run_summary(state: PipelineState) -> dict:
    """Compact per-run metrics written to traces.jsonl and shown in the UI."""
    results = state.get("claim_results", [])
    passed = sum(1 for r in results if r["status"] == "pass")
    dropped = sum(1 for r in results if r["status"] == "dropped")
    checked = passed + dropped

    critiques = state.get("editor_critiques", {})
    rounds = sorted({c["round"] for cs in critiques.values() for c in cs})
    reject_by_round = {}
    for r in rounds:
        total = len(cs := [c for lst in critiques.values() for c in lst if c["round"] == r])
        rejected = sum(1 for c in cs if c["verdict"] == "reject")
        reject_by_round[f"v{r}"] = round(rejected / total * 100, 1) if total else 0.0

    tele = state.get("telemetry", [])
    return {
        "topic": state.get("topic", ""),
        "mock": state.get("mock", True),
        "fact": {
            "claims_checked": checked,
            "pass_pct": round(passed / checked * 100, 1) if checked else None,
            "dropped": dropped,
            "retries": state.get("fact_retries", 0),
        },
        "editor": {
            "reject_pct_by_round": reject_by_round,
            "rounds": state.get("editor_round", 0),
        },
        "guardrail": {
            "sources_quarantined": len(state.get("guardrail_sources", {}).get("injections_blocked", [])),
            "pii_redactions": state.get("guardrail_outputs", {}).get("pii_redactions", 0),
            "citation_errors": len(state.get("guardrail_outputs", {}).get("citation_errors", [])),
        },
        "versions": {p: len(v) for p, v in state.get("drafts", {}).items()},
        "virality": {
            "by_platform": {p: r.get("score") for p, r in state.get("virality", {}).items()},
            "average": round(sum(r.get("score", 0) for r in state.get("virality", {}).values()) /
                             len(state.get("virality", {})), 1) if state.get("virality") else None,
        },
        "latency_ms": round(sum(t["ms"] for t in tele), 1),
        "tokens": sum(t.get("tokens", 0) for t in tele),
        "nodes": [t["node"] for t in tele],
    }


def _trace_text(text: str) -> str:
    """Keep raw content out of persistent traces unless explicitly enabled."""
    if config.TRACE_RAW_CONTENT:
        return text
    from .guardrail import redact_pii
    safe, _ = redact_pii(text)
    return safe


def _trace_sources(sources: list) -> list:
    out = []
    for source in sources:
        item = dict(source)
        item["title"] = _trace_text(str(item.get("title", "")))
        item["url"] = _trace_text(str(item.get("url", "")))
        snippet = _trace_text(str(item.get("snippet", "")))
        item["snippet"] = snippet if config.TRACE_RAW_CONTENT else snippet[:2000]
        out.append(item)
    return out


def _trace_drafts(drafts: dict) -> dict:
    return {
        platform: [dict(version, text=_trace_text(str(version.get("text", ""))))
                   for version in versions]
        for platform, versions in drafts.items()
    }


def _trace_records(records: list, fields: tuple[str, ...]) -> list:
    out = []
    for record in records:
        item = dict(record)
        for field in fields:
            if field in item:
                item[field] = _trace_text(str(item[field]))
        out.append(item)
    return out


def tracker_node(state: PipelineState) -> dict:
    t0 = time.perf_counter()
    summary = run_summary(state)
    trace = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summary,
        "full_state": {
            "topic": state.get("topic", ""),
            "platforms": state.get("platforms", []),
            "mock": state.get("mock", True),
            "research_round": state.get("research_round", 0),
            "sources": _trace_sources(state.get("sources", [])),
            "drafts": _trace_drafts(state.get("drafts", {})),
            "claim_results": _trace_records(state.get("claim_results", []), ("claim", "reason")),
            "failed_claims": _trace_records(state.get("failed_claims", []), ("claim", "reason")),
            "dropped_claims": _trace_records(state.get("dropped_claims", []), ("claim", "reason")),
            "fact_pass": state.get("fact_pass", False),
            "fact_retries": state.get("fact_retries", 0),
            "editor_critiques": {
                p: _trace_records(cs, ("critique",))
                for p, cs in state.get("editor_critiques", {}).items()
            },
            "editor_round": state.get("editor_round", 0),
            "rejected_platforms": state.get("rejected_platforms", []),
            "guardrail_sources": state.get("guardrail_sources", {}),
            "guardrail_outputs": state.get("guardrail_outputs", {}),
            "telemetry": state.get("telemetry", []),
            "log": [_trace_text(str(line)) for line in state.get("log", [])],
            "outputs": {p: _trace_text(str(t)) for p, t in state.get("outputs", {}).items()},
            "virality": state.get("virality", {}),
            "trace_raw_content": config.TRACE_RAW_CONTENT,
        },
    }
    try:
        with config.TRACES_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(trace, ensure_ascii=False) + "\n")
    except OSError:
        pass  # tracing must never fail the pipeline
    return {
        "telemetry": [telem("tracker", t0, detail=f"trace -> {config.TRACES_PATH.name}")],
        "log": [f"[tracker] run complete: {summary['fact']['pass_pct']}% facts pass, "
                f"latency {summary['latency_ms'] / 1000:.1f}s, tokens {summary['tokens']}"],
    }
