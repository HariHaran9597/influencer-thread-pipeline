"""Editor node: scores clarity / tone / platform-fit 1-5 + actionable critique.

Approve rule (deliberately stricter than a plain mean): mean >= 4.0 AND no
single dimension below 3.5 — a great average cannot hide one broken dimension.
The writer<->editor loop is capped at MAX_EDITOR_ROUNDS; whatever the state of
the draft at the cap ships (and the cap-hitting is visible in traces).
"""
from __future__ import annotations

import re
import time

from . import config
from .llm import _build_judge, call_llm, estimate_tokens, parse_json_loose
from .state import EditorCritique, PipelineState
from .tracker import telem

LENGTH_TARGETS = {  # (min, max) — words for linkedin/instagram, chars for twitter
    "linkedin": (100, config.LINKEDIN_MAX_WORDS),
    "twitter": (300, 900),
    "instagram": (40, config.INSTAGRAM_MAX_WORDS),
}

_HOOK_RE = re.compile(
    r"^\s*(?:here'?s|how|why|what|when|who|which|stop|nobody|everyone|\d+[%\w]?)\b",
    re.IGNORECASE,
)
_CITE_RE = re.compile(r"\[(\d+)\]")


def _length_value(platform: str, text: str) -> float:
    lo, hi = LENGTH_TARGETS[platform]
    n = len(text) if platform == "twitter" else len(text.split())
    if lo <= n <= hi:
        return 5.0
    if lo * 0.6 <= n <= hi * 1.3:
        return 4.0
    return 2.0


def heuristic_scores(platform: str, text: str, n_sources: int) -> dict:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    hook = 5.0 if (_HOOK_RE.match(first_line) or "?" in first_line) else 1.0
    cites = _CITE_RE.findall(text)
    valid = [c for c in cites if 1 <= int(c) <= n_sources]
    citation_score = 5.0 if len(valid) >= 2 else (4.0 if len(valid) == 1 else 1.0)
    length = _length_value(platform, text)
    clarity, tone, fit = length, citation_score, hook
    problems = []
    if fit < 3.5:
        problems.append("opening line is not a hook — start with a number, a question, "
                        "or 'Here's what N sources actually say…'")
    if clarity < 3.5:
        lo, hi = LENGTH_TARGETS[platform]
        unit = "characters" if platform == "twitter" else "words"
        n = len(text) if platform == "twitter" else len(text.split())
        problems.append(f"length {n} {unit} outside target {lo}-{hi}")
    if citation_score < 3.5:
        problems.append("fewer than 2 valid [n] citations")
    return {
        "clarity": clarity, "tone": tone, "platform_fit": fit,
        "critique": "; ".join(problems) if problems else "tight, cited, on-format",
    }


def llm_scores(platform: str, text: str, topic: str) -> tuple[dict, int]:
    judge = _build_judge()
    system = (
        "You are a ruthless content editor scoring a draft post. Score clarity, tone, and "
        "platform_fit each from 1.0 to 5.0 (halves allowed). platform_fit means: does it "
        "look native to the platform (hook, pacing, length, format)? In critique, name the "
        "single biggest fix in one sentence. "
        'Reply JSON only: {"clarity": f, "tone": f, "platform_fit": f, "critique": "..."}'
    )
    user = (f"TOPIC: {topic}\nPLATFORM: {platform}\n\nDRAFT:\n{text}")
    out, tokens = call_llm(judge, system, user)
    parsed = parse_json_loose(out) or {}
    d = {k: float(parsed.get(k, 3.0)) for k in ("clarity", "tone", "platform_fit")}
    d["clarity"] = min(d["clarity"], 5.0); d["tone"] = min(d["tone"], 5.0); d["platform_fit"] = min(d["platform_fit"], 5.0)
    d["critique"] = str(parsed.get("critique", ""))[:300]
    return d, tokens


def _verdict(d: dict) -> str:
    dims = [d["clarity"], d["tone"], d["platform_fit"]]
    return "approve" if (sum(dims) / 3 >= config.EDITOR_APPROVE_SCORE
                         and min(dims) >= 3.5) else "reject"


def editor_node(state: PipelineState) -> dict:
    t0 = time.perf_counter()
    mock = state.get("mock", True)
    rnd = state.get("editor_round", 0) + 1
    n_sources = len(state.get("sources", []))
    critiques = {p: list(v) for p, v in state.get("editor_critiques", {}).items()}
    tokens = 0
    rejected: list[str] = []

    for platform in state["platforms"]:
        versions = state.get("drafts", {}).get(platform, [])
        text = versions[-1]["text"] if versions else ""
        if mock or _build_judge() is None:
            d = heuristic_scores(platform, text, n_sources)
            tokens += estimate_tokens(text)
        else:
            try:
                d, tok = llm_scores(platform, text, state["topic"])
                tokens += tok
            except Exception:                 # judge died -> deterministic scores
                d = heuristic_scores(platform, text, n_sources)
        critique = EditorCritique(
            round=rnd, platform=platform,
            clarity=d["clarity"], tone=d["tone"], platform_fit=d["platform_fit"],
            verdict=_verdict(d), critique=d["critique"],
        )
        critiques.setdefault(platform, []).append(critique)
        if critique["verdict"] == "reject":
            rejected.append(platform)

    return {
        "editor_critiques": critiques,
        "editor_round": rnd,
        "rejected_platforms": rejected,
        "telemetry": [telem("editor", t0, tokens,
                            detail=f"round {rnd}: {len(rejected)} rejected")],
        "log": [f"[editor] round {rnd}: " + ", ".join(
            f"{c['platform']} {'✓' if c['verdict'] == 'approve' else '✗'}"
            f" ({c['clarity']:.0f}/{c['tone']:.0f}/{c['platform_fit']:.0f})"
            for cs in critiques.values() for c in cs if c["round"] == rnd)]
    }
