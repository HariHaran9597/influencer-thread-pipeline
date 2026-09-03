"""Fact-checker node: claim-vs-source entailment with a retry cap.

Design decisions (from the project review):
  - The judge is a DIFFERENT model than the writer (see llm.py) so the fact
    score is not self-graded.
  - MAX_FACT_RETRIES caps the loop; after that, unverifiable claims are
    DROPPED from the draft (deterministic sentence removal) instead of
    cycling forever. Dropped-claim count is itself a reported metric.
"""
from __future__ import annotations

import re
import time

from . import config
from .llm import call_llm, estimate_tokens, judge_provider, parse_json_loose, _build_judge
from .state import ClaimResult, PipelineState
from .tracker import telem

_CLAIM_RE = re.compile(r"([^\[\]]+?)\s*\[(\d+)\]")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")   # line breaks also end a "sentence"
_STOP = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "of", "to", "in", "on", "for", "with", "at", "by", "as", "this", "that", "it",
    "its", "has", "have", "had", "will", "from", "into", "than", "then", "there",
    "their", "they", "you", "your", "we", "our", "about", "what", "which", "who",
}


def extract_claims(text: str) -> list[tuple[str, int]]:
    """[(claim_sentence, source_index)] for every sentence carrying a [n] cite.
    Only the sentence immediately before the citation counts as the claim."""
    claims: list[tuple[str, int]] = []
    for m in _CLAIM_RE.finditer(text):
        before = m.group(1).strip(" \n\t-•")
        if not before:
            continue
        sentence = [s for s in _SENT_SPLIT.split(before) if s.strip()][-1]
        sentence = sentence.strip().lstrip("•- ").strip()
        if len(sentence.split()) >= 3:              # ignore fragments
            claims.append((sentence, int(m.group(2))))
    return claims


def _content_words(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower())
            if len(t) > 2 and t not in _STOP}


def heuristic_entailment(claim: str, source_text: str) -> tuple[float, str]:
    """Deterministic fallback judge: fraction of the claim's content words
    (numbers included) present in the cited source. Free and offline."""
    cw = _content_words(claim)
    if not cw:
        return 1.0, "no content words to verify"
    sw = _content_words(source_text)
    hit = cw & sw
    score = min(1.0, len(hit) / len(cw))
    missing = ", ".join(sorted(cw - sw)[:5]) or "none"
    return score, f"claim words covered by source: {len(hit)}/{len(cw)} (missing: {missing})"


def judge_entailment(claim: str, source_text: str) -> tuple[float, str]:
    return judge_entailment_batch([(claim, source_text)])[0]


def judge_entailment_batch(pairs: list[tuple[str, str]]) -> list[tuple[float, str]]:
    """Score many claim/source pairs in ONE judge call (the per-claim loop was
    ~60 LLM calls per run and dominated latency). Falls back to per-claim
    scoring, then to the heuristic, if the batch call or parse fails."""
    if not pairs:
        return []
    judge = _build_judge()
    if judge is None:
        return [heuristic_entailment(c, s) for c, s in pairs]
    system = (
        "You are a strict fact-checking judge. For each numbered CLAIM, does its SOURCE "
        "entail it? Score 1.0 only if every part of the claim (numbers, dates, names) is "
        "directly supported; 0.5-0.9 if partial; 0.0-0.4 if contradicted or unverifiable. "
        'Reply with JSON only: {"results": [{"index": 0, "score": <float>, "reason": "..."}, ...]}'
    )
    parts = [f"--- CLAIM {i} ---\n{c}\n--- SOURCE {i} ---\n{s[:2500]}"
             for i, (c, s) in enumerate(pairs)]
    try:
        text, _tok = call_llm(judge, system, "\n\n".join(parts))
        parsed = parse_json_loose(text)
        items = parsed.get("results") if parsed else None
        if items and len(items) == len(pairs):
            out = []
            for i, it in enumerate(items):
                if isinstance(it.get("score"), (int, float)):
                    out.append((float(it["score"]), str(it.get("reason", ""))[:200]))
                else:
                    out.append(heuristic_entailment(*pairs[i]))
            return out
    except Exception:
        pass
    return [heuristic_entailment(c, s) for c, s in pairs]


def _latest_drafts(state: PipelineState) -> dict[str, str]:
    return {p: versions[-1]["text"] for p, versions in state.get("drafts", {}).items()
            if versions}


def fact_checker_node(state: PipelineState) -> dict:
    t0 = time.perf_counter()
    sources = state.get("sources", [])
    drafts = _latest_drafts(state)

    # union of claims across platforms, deduped, renumbered by source order
    seen: dict[tuple[str, int], None] = {}
    for text in drafts.values():
        for claim, n in extract_claims(text):
            seen.setdefault((claim, n), None)
    pairs = list(seen.keys())

    results: list[ClaimResult] = []
    pending: list[int] = []            # indices into results needing a judge score
    for claim, n in pairs:
        if 1 <= n <= len(sources):
            results.append(ClaimResult(claim=claim, citation=n, score=0.0,
                                       reason="", status="fail"))
            pending.append(len(results) - 1)
        else:
            results.append(ClaimResult(claim=claim, citation=n, score=0.0,
                                       reason=f"cited source [{n}] does not exist",
                                       status="fail"))
    if pending:
        scored = judge_entailment_batch(
            [(results[i]["claim"],
              f"{sources[results[i]['citation'] - 1]['title']}. "
              f"{sources[results[i]['citation'] - 1]['snippet']}")
             for i in pending])
        for i, (score, reason) in zip(pending, scored):
            results[i]["score"] = round(score, 3)
            results[i]["reason"] = reason
            results[i]["status"] = "pass" if score >= config.FACT_PASS_THRESHOLD else "fail"

    failed = [r for r in results if r["status"] == "fail"]
    retries = state.get("fact_retries", 0)
    tokens = estimate_tokens(" ".join(c for c, _ in pairs)) if judge_provider() == "heuristic" else 0

    # ---- routing decision: retry -> researcher | drop claims -> proceed ----
    if failed and retries < config.MAX_FACT_RETRIES:
        return {
            "claim_results": results,
            "failed_claims": failed,
            "fact_pass": False,
            "fact_retries": retries + 1,
            "telemetry": [telem("fact_checker", t0, tokens,
                                detail=f"{len(failed)}/{len(results)} claims failed (retry)")],
            "log": [f"[fact-checker] {len(failed)}/{len(results)} claims below "
                    f"{config.FACT_PASS_THRESHOLD} -> back to researcher (retry {retries + 1}/"
                    f"{config.MAX_FACT_RETRIES}): " + "; ".join(r['claim'][:60] for r in failed[:3])],
        }

    if failed:  # retries exhausted: drop the unverifiable sentences deterministically
        drop_phrases = {r["claim"] for r in failed}
        for r in failed:
            r["status"] = "dropped"
        drafts_after: dict[str, list] = {}
        for platform, versions in state.get("drafts", {}).items():
            latest = versions[-1]
            cleaned = latest["text"]
            for phrase in drop_phrases:
                cleaned = _remove_sentence(cleaned, phrase)
            drafts_after[platform] = versions + [{
                "version": latest["version"] + 1,
                "text": cleaned,
                "note": f"dropped {len(drop_phrases)} unverifiable claim(s)",
            }]
        log = (f"[fact-checker] retries exhausted -> dropped {len(failed)} claim(s) "
               "from all drafts")
        return {
            "claim_results": results,
            "dropped_claims": failed,
            "failed_claims": [],
            "fact_pass": True,
            "drafts": drafts_after,
            "telemetry": [telem("fact_checker", t0, tokens, detail=f"dropped {len(failed)} claims")],
            "log": [log],
        }

    return {
        "claim_results": results,
        "failed_claims": [],
        "fact_pass": True,
        "telemetry": [telem("fact_checker", t0, tokens, detail=f"all {len(results)} claims pass")],
        "log": [f"[fact-checker] all {len(results)} claims entailed "
                f"(>= {config.FACT_PASS_THRESHOLD})"],
    }


def _remove_sentence(text: str, sentence: str) -> str:
    """Remove the sentence AND its trailing [n] citation from a draft."""
    esc = re.escape(sentence)
    cleaned = re.sub(rf"\s*{esc}\s*\[\d+\]", " ", text)
    if cleaned == text:                       # citation was placed differently
        cleaned = text.replace(sentence, "")
    return re.sub(r"[ \t]{2,}", " ", cleaned).replace(" \n", "\n")
