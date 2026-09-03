"""Guardrails: prompt-injection detection in sources, PII redaction in output,
citation hallucination verification ([n] must reference a real, non-quarantined
source). Two nodes: guardrail_sources runs BEFORE the writer ever sees the
research bundle; guardrail_outputs runs on the final formatted posts.
"""
from __future__ import annotations

import re
import time

from . import config
from .state import GuardrailReport, PipelineState, Source
from .tracker import telem

# --------------------------------------------------------- injections ----
INJECTION_PATTERNS: list[tuple[str, str]] = [
    ("override-previous", r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?|rules?|context)"),
    ("disregard-above", r"disregard\s+(?:the\s+|all\s+|any\s+|your\s+)?(?:above|previous|prior|guidelines|instructions|rules)"),
    ("forget-context", r"forget\s+(?:everything|all|your)\s+(?:above|previous|prior|instructions)"),
    ("role-reset", r"you\s+are\s+now\s+(?:a|an|the|DAN)"),
    ("system-prompt-probe", r"(?:reveal|print|show|output|repeat|append|expose|dump|leak)\s+(?:your|the)\s+(?:full\s+)?(?:system\s+)?(?:initial\s+)?(?:prompt|instructions?|rules|template)"),
    ("developer-mode", r"developer\s+mode"),
    ("jailbreak", r"jailbreak|DAN\s+mode"),
    ("override-safety", r"override\s+(?:all\s+|the\s+|your\s+)?(?:safety|instructions|rules|filters)"),
    ("unrestricted-role", r"(?:unrestricted|unfiltered|no\s+content\s+polic(?:y|ies))"),
    ("new-rules", r"new\s+rules?\s*[:\-]"),
    ("respond-only", r"respond\s+only\s+with"),
    ("hidden-instruction", r"\[?\s*(?:internal|hidden|secret)\s+(?:instructions?|rules?)\s*[.:]"),
    ("fake-tag", r"</?(?:system|assistant|developer)\s*>"),
    ("markup-injection", r"<!--.{0,200}(?:ignore|instruction|prompt)"),
    ("suppress-disclosure", r"do\s+not\s+tell\s+the\s+user"),
]

_CACHED_RE = [(name, re.compile(p, re.IGNORECASE | re.DOTALL)) for name, p in INJECTION_PATTERNS]


def detect_injection(text: str) -> list[tuple[str, str]]:
    """Returns [(pattern_name, excerpt)] for every injection signature found."""
    hits = []
    for name, rx in _CACHED_RE:
        m = rx.search(text)
        if m:
            excerpt = m.group(0)[:80].replace("\n", " ")
            hits.append((name, excerpt))
    return hits


# ---------------------------------------------------------------- PII ----
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_IN = re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)")            # Indian mobile
_PHONE_INTL = re.compile(r"(?<!\d)\+\d{1,3}[\s-]\d{2,4}[\s-]\d{6,10}(?!\d)")  # +CC format
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_AADHAAR = re.compile(r"\b[2-9]\d{3}[\s]\d{4}[\s]\d{4}\b")


def _luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits]
    if len(d) < 13:
        return False
    total = 0
    for i, c in enumerate(reversed(d)):
        if i % 2 == 1:
            c *= 2
            if c > 9:
                c -= 9
        total += c
    return total % 10 == 0


_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def redact_pii(text: str) -> tuple[str, int]:
    """Returns (redacted_text, redaction_count). Over-redaction is the safe side."""
    count = 0

    def _sub(rx: re.Pattern, repl: str = "[REDACTED]") -> None:
        nonlocal text, count
        n = len(rx.findall(text))
        if n:
            text = rx.sub(repl, text)
            count += n

    _sub(_EMAIL)
    _sub(_PHONE_INTL)
    _sub(_PHONE_IN)
    _sub(_SSN)
    _sub(_AADHAAR)

    cards = [m for m in _CARD.finditer(text) if _luhn_ok(re.sub(r"[ -]", "", m.group(0)))]
    for m in reversed(cards):
        text = text[:m.start()] + "[REDACTED]" + text[m.end():]
        count += 1
    return text, count


# ------------------------------------------------------------ citations ---
_CITE = re.compile(r"\[(\d+)\]")


def find_citations(text: str) -> list[int]:
    return [int(n) for n in _CITE.findall(text)]


def verify_citations(text: str, platform: str, n_sources: int,
                     quarantined: set[int]) -> list[dict]:
    errors = []
    for n in find_citations(text):
        if n < 1 or n > n_sources:
            errors.append({"platform": platform, "token": f"[{n}]",
                           "problem": f"no source {n} (bundle has {n_sources})"})
        elif n in quarantined:
            errors.append({"platform": platform, "token": f"[{n}]",
                           "problem": f"source {n} was quarantined for prompt injection"})
    return errors


# --------------------------------------------------------------- nodes ----
def guardrail_sources_node(state: PipelineState) -> dict:
    t0 = time.perf_counter()
    blocked, clean, quarantined_idx = [], [], set()
    for i, s in enumerate(state.get("sources", []), start=1):
        hits = detect_injection(f"{s['title']}\n{s['snippet']}")
        if hits:
            quarantined_idx.add(i)
            blocked.append({"source_index": i, "source_id": s["source_id"], "url": s["url"],
                            "pattern": hits[0][0], "excerpt": hits[0][1]})
        else:
            clean.append(s)
    report: GuardrailReport = {
        "injections_blocked": blocked,
        "quarantined_urls": [b["url"] for b in blocked],
        "quarantined_source_ids": [b["source_id"] for b in blocked],
        "pass_": True,
    }
    log = [f"[guardrail/sources] quarantined {len(blocked)} source(s): "
           + ", ".join(b["pattern"] for b in blocked)] if blocked else \
          ["[guardrail/sources] clean — no injection signatures in bundle"]
    return {
        "sources": clean,
        "guardrail_sources": report,
        "telemetry": [telem("guardrail_sources", t0, detail=f"{len(blocked)} quarantined")],
        "log": log,
    }


def guardrail_outputs_node(state: PipelineState) -> dict:
    t0 = time.perf_counter()
    quarantined_ids = set(state.get("guardrail_sources", {}).get("quarantined_source_ids", []))
    # Citations are intentionally display-oriented 1-based positions. Resolve
    # quarantine status through stable source IDs so removing a blocked source
    # cannot shift the meaning of every later citation.
    quarantined = {
        i for i, source in enumerate(state.get("sources", []), start=1)
        if source.get("source_id") in quarantined_ids
    }
    n_sources = len(state.get("sources", []))

    outputs, citation_errors, pii_total = {}, [], 0
    for platform, text in state.get("outputs", {}).items():
        safe, n = redact_pii(text)
        pii_total += n
        citation_errors.extend(verify_citations(safe, platform, n_sources, quarantined))
        outputs[platform] = safe
    report: GuardrailReport = {
        "pii_redactions": pii_total,
        "citation_errors": citation_errors,
        "pass_": not citation_errors,
    }
    return {
        "outputs": outputs,
        "guardrail_outputs": report,
        "telemetry": [telem("guardrail_outputs", t0,
                            detail=f"{pii_total} PII, {len(citation_errors)} cite errors")],
        "log": [f"[guardrail/outputs] {pii_total} PII redaction(s), "
                f"{len(citation_errors)} citation error(s)"],
    }
