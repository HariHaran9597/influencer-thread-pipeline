"""LangGraph state: the full audit trail, not just the final text.

research bundle + every draft version + per-claim fact scores + every editor
critique + guardrail report + telemetry. All of it survives to reports/traces.jsonl.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class Source(TypedDict):
    source_id: str          # stable provenance key; citations remain 1-based for display
    title: str
    url: str
    snippet: str
    date: str


class Draft(TypedDict):
    version: int
    text: str
    note: str          # why this version exists ("v1", "after fact-check drop", ...)


class ClaimResult(TypedDict):
    claim: str
    citation: int      # 1-based source index the draft cited
    score: float       # 0-1 entailment vs that source
    reason: str
    status: str        # "pass" | "fail" | "dropped"


class EditorCritique(TypedDict):
    round: int
    platform: str
    clarity: float
    tone: float
    platform_fit: float
    verdict: str       # "approve" | "reject"
    critique: str


class GuardrailReport(TypedDict, total=False):
    injections_blocked: list[dict]    # {source_index, pattern, excerpt}
    pii_redactions: int
    citation_errors: list[dict]       # {platform, token, problem}
    quarantined_urls: list[str]
    quarantined_source_ids: list[str]
    pass_: bool


class Telemetry(TypedDict, total=False):
    node: str
    ms: float
    tokens: int
    detail: str


class PipelineState(TypedDict, total=False):
    # input
    topic: str
    platforms: list[str]                       # subset of {"linkedin","twitter","instagram"}
    mock: bool                                 # True if this run used mock LLM/search

    # research
    sources: list[Source]
    research_round: int                        # 0 on first pass, +1 per fact-check retry

    # writing (platform -> every version, newest last)
    drafts: dict[str, list[Draft]]
    writer_feedback: str                       # critique/failed claims fed back in

    # fact check
    claim_results: list[ClaimResult]
    failed_claims: list[ClaimResult]
    dropped_claims: list[ClaimResult]
    fact_pass: bool
    fact_retries: int

    # editing
    editor_critiques: dict[str, list[EditorCritique]]   # platform -> critiques per round
    editor_round: int
    rejected_platforms: list[str]

    # output
    outputs: dict[str, str]                    # platform -> final formatted text
    sources_footer: str
    virality: dict[str, dict]                  # platform -> content-readiness strategy

    # safety
    guardrail_sources: GuardrailReport
    guardrail_outputs: GuardrailReport

    # bookkeeping (append-only across nodes)
    telemetry: Annotated[list[Telemetry], operator.add]
    log: Annotated[list[str], operator.add]
