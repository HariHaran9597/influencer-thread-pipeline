"""Deterministic, evidence-aware virality strategy.

This is a content-readiness heuristic, not a prediction of audience reach.
It rewards strong hooks, concrete evidence, interaction prompts, and native
formatting while explicitly avoiding unsupported sensationalism.
"""
from __future__ import annotations

import re
import time

from .state import PipelineState
from .tracker import telem

_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")
_CITE_RE = re.compile(r"\[\d+\]")
_QUESTION_RE = re.compile(r"\?|\b(comment|what would you|which|would you)\b", re.I)
_HOOK_RE = re.compile(
    r"^\s*(?:here'?s|how|why|what|stop|nobody|everyone|the first|\d+[%\w]?)\b",
    re.I,
)


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _native_score(platform: str, text: str) -> float:
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if platform == "twitter":
        lengths = [len(p) for p in paragraphs]
        return 5.0 if 5 <= len(paragraphs) <= 10 and lengths and max(lengths) <= 280 else 3.5
    if platform == "instagram":
        return 5.0 if "\n" in text and len(_CITE_RE.findall(text)) >= 2 else 3.5
    return 5.0 if len(paragraphs) >= 4 else 3.5


def _score(platform: str, text: str, source_count: int) -> dict:
    hook = 5.0 if _HOOK_RE.search(_first_line(text)) or "?" in _first_line(text) else 2.5
    specificity = min(5.0, 2.5 + min(2.5, len(_NUMBER_RE.findall(text)) * 0.5))
    evidence = min(5.0, 2.5 + min(2.5, len(_CITE_RE.findall(text)) / max(1, source_count) * 2.5))
    interaction = 5.0 if _QUESTION_RE.search(text) else 2.5
    native = _native_score(platform, text)
    dimensions = {
        "hook": round(hook, 1),
        "specificity": round(specificity, 1),
        "evidence": round(evidence, 1),
        "interaction": round(interaction, 1),
        "native_format": round(native, 1),
    }
    score = round(sum(dimensions.values()) / 25 * 100)

    recommendations: list[str] = []
    if hook < 4:
        recommendations.append("Replace the opener with one verified number, tension, or question.")
    if specificity < 4:
        recommendations.append("Lead with the clearest concrete result instead of a broad claim.")
    if interaction < 4:
        recommendations.append("End with one focused question that invites a real opinion.")
    if native < 4:
        recommendations.append("Use shorter, platform-native units so the first screen carries the idea.")
    if not recommendations:
        recommendations.append("Keep the verified hook, then test two opening lines with the same evidence.")

    angles = {
        "linkedin": "Verified insight → practical implication → invite practitioners to disagree.",
        "twitter": "Number-led first post → one cited idea per post → open loop into the next post.",
        "instagram": "Saveable carousel → one cited takeaway per slide → caption question for comments.",
    }
    return {
        "score": score,
        "label": "strong foundation" if score >= 75 else "needs a sharper angle" if score >= 55 else "rewrite the hook",
        "dimensions": dimensions,
        "angle": angles[platform],
        "recommendations": recommendations[:3],
        "guardrail": "Use only verified claims; never add fake urgency, scarcity, or engagement bait.",
    }


def virality_node(state: PipelineState) -> dict:
    """Score final safe outputs and attach actionable platform guidance."""
    t0 = time.perf_counter()
    sources = state.get("sources", [])
    reports = {
        platform: _score(platform, text, len(sources))
        for platform, text in state.get("outputs", {}).items()
    }
    average = round(sum(r["score"] for r in reports.values()) / len(reports), 1) if reports else None
    return {
        "virality": reports,
        "telemetry": [telem("virality", t0, detail=f"{average if average is not None else '—'}/100 readiness")],
        "log": ["[virality] content-readiness scores: " + ", ".join(
            f"{p} {r['score']}/100" for p, r in reports.items())],
    }
