"""Writer node: cited drafts per platform, revised on fact-check/editor feedback.

Mock mode builds drafts from real (mock-corpus) snippets deterministically:
  v1 deliberately ships one fabricated claim + a weak opener, so the graph's
  fact-check loop and editor loop are exercised end-to-end offline for free.
Live mode prompts the writer LLM with the numbered research bundle and any
pending feedback (failed claims to remove, editor critique to address).
"""
from __future__ import annotations

import re
import time

from . import config
from .llm import _build_writer, call_llm, estimate_tokens, writer_available
from .state import PipelineState
from .tracker import telem

PLATFORMS = ("linkedin", "twitter", "instagram")

PLATFORM_BRIEF = {
    "linkedin": (
        "A LinkedIn post for a professional audience. 150-500 words. Open with a strong hook "
        "(a number, question, or contrarian statement), then short paragraphs. End with a "
        "question that invites comments. Professional but human tone."
    ),
    "twitter": (
        "Source material for a Twitter/X thread. 400-800 characters total across 5-8 short "
        "paragraphs (one idea each, no numbering - the formatter adds it). First paragraph "
        "must be a scroll-stopping hook."
    ),
    "instagram": (
        "An Instagram caption. 60-140 words, line breaks between ideas, 1-2 emojis maximum. "
        "Do NOT add hashtags - the formatter generates them."
    ),
}


# ------------------------------------------------------------ live ----
def _live_draft(topic: str, platform: str, sources: list, feedback: str,
                prev_draft: str) -> tuple[str, int]:
    llm = _build_writer()
    bundle = "\n".join(f"[{i}] {s['title']} ({s['date'] or 'n.d.'})\n{s['snippet']}"
                       for i, s in enumerate(sources, 1))
    user = (
        f"TOPIC: {topic}\n\nRESEARCH BUNDLE (the ONLY facts you may use):\n{bundle}\n\n"
        f"PLATFORM: {platform} — {PLATFORM_BRIEF[platform]}\n"
    )
    if feedback:
        user += (
            f"\nYou are REVISING this draft. Fix every issue below:\n{feedback}\n\n"
            f"PREVIOUS DRAFT:\n{prev_draft}\n"
        )
    user += "\nReturn only the post text, with [n] citations on factual sentences."
    return call_llm(llm, _WRITER_SYSTEM, user)


_WRITER_SYSTEM = (
    "You are a professional social media content writer. You use ONLY the provided "
    "research bundle: every factual sentence must end with a citation [n] matching "
    "source n. Never invent facts, numbers, dates, or quotes. If something is not in "
    "the bundle, leave it out rather than guess."
)


# ------------------------------------------------------------ mock ----
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _claim_sentences(snippet: str, n: int) -> list[str]:
    sents = [s.strip() for s in _SENT_SPLIT.split(snippet) if s.strip()]
    return sents[:n]


def _mock_draft(state: PipelineState, platform: str, sources: list) -> str:
    topic = state["topic"]
    use_hook = state.get("editor_round", 0) >= 1        # editor asked for a revision
    fabricate = state.get("fact_retries", 0) == 0       # v1 ships one bad claim on purpose

    per_source = {"linkedin": 2, "twitter": 1, "instagram": 1}[platform]
    banned = {r["claim"] for r in state.get("failed_claims", [])}
    claims: list[tuple[str, int]] = []
    # A fact-check retry must actually expose the newly researched window to
    # the deterministic writer; otherwise the loop only exercises routing.
    window = sources[:5] if state.get("fact_retries", 0) == 0 else sources[-5:]
    offset = len(sources) - len(window) if state.get("fact_retries", 0) else 0
    for i, s in enumerate(window, offset + 1):
        for sent in _claim_sentences(s["snippet"], per_source):
            if sent not in banned:               # honor fact-checker verdicts
                claims.append((sent, i))

    if fabricate:
        claims.insert(2, (f"Industry insiders confirm expectations around {topic} "
                           f"are at an all-time high this cycle.", len(sources) + 1))

    if use_hook:
        opener = f"Here's what {min(5, len(sources))} sources actually say about {topic}:"
    else:
        opener = f"{topic} — everything you need to know."

    if platform == "linkedin":
        body = "\n\n".join(f"{c} [{n}]" for c, n in claims)
        closer = "The pattern across every source is the same: verify before you amplify.\n\nWhat would you add? Drop your take in the comments."
    elif platform == "twitter":
        body = "\n\n".join(f"{c} [{n}]" for c, n in claims)
        closer = "Full breakdown with sources below 🧵"
    else:
        body = "\n".join(f"• {c} [{n}]" for c, n in claims)
        closer = "💬 Which one surprised you?"
    return f"{opener}\n\n{body}\n\n{closer}"


# ----------------------------------------------------------- node ----
def _feedback_text(state: PipelineState) -> str:
    parts = []
    failed = state.get("failed_claims", [])
    if failed:
        parts.append("The fact-checker rejected these claims as unverifiable — remove them "
                     "and do not replace them with new unverified statements:\n- "
                     + "\n- ".join(f'"{r["claim"]}"' for r in failed))
    critiques = state.get("editor_critiques", {})
    rejected = state.get("rejected_platforms", [])
    for p in rejected:
        for c in reversed(critiques.get(p, [])):
            if c["verdict"] == "reject":
                parts.append(f"[{p}] editor critique (clarity {c['clarity']}, tone {c['tone']}, "
                             f"platform-fit {c['platform_fit']}): {c['critique']}")
                break
    return "\n\n".join(parts)


def writer_node(state: PipelineState) -> dict:
    t0 = time.perf_counter()
    mock = state.get("mock", True) or not writer_available()
    sources = state.get("sources", [])
    feedback = _feedback_text(state)
    drafts = {p: list(v) for p, v in state.get("drafts", {}).items()}
    tokens = 0
    fell_back = False

    for platform in state["platforms"]:
        versions = drafts.get(platform, [])
        prev = versions[-1]["text"] if versions else ""
        if mock:
            text = _mock_draft(state, platform, sources)
            tokens += estimate_tokens(text)
        else:
            try:
                text, tok = _live_draft(state["topic"], platform, sources, feedback, prev)
                tokens += tok
            except Exception as exc:          # live call died mid-run -> degrade, don't crash
                fell_back = True
                text = _mock_draft(state, platform, sources)
                tokens += estimate_tokens(text)
                _ = exc
        note = "v1 draft"
        if failed := state.get("failed_claims"):
            note = f"v{len(versions) + 1}: removed {len(failed)} unverifiable claim(s)"
        elif state.get("rejected_platforms"):
            note = f"v{len(versions) + 1}: revised after editor critique"
        versions.append({"version": len(versions) + 1, "text": text, "note": note})
        drafts[platform] = versions

    log_note = " (mock writer)" if mock else (" (live writer degraded to mock!)" if fell_back else "")
    return {
        "drafts": drafts,
        "writer_feedback": feedback,
        "telemetry": [telem("writer", t0, tokens, detail=f"{len(state['platforms'])} platform(s){log_note}")],
        "log": [f"[writer] drafted/revised {', '.join(state['platforms'])}{log_note}"
                + (f" | feedback applied: {feedback[:80]}…" if feedback else "")],
    }
