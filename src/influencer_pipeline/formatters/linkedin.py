"""LinkedIn formatter: enforce the 600-word cap by trimming trailing sentences
(never the hook), and keep citations inline. The sources footer is appended by
the formatter node and is not counted against the word budget."""
from __future__ import annotations

import re

_SENT = re.compile(r"(?<=[.!?])\s+")


def format_linkedin(draft: str, max_words: int = 600) -> str:
    text = draft.strip()
    if len(text.split()) <= max_words:
        return text
    sentences = _SENT.split(text)
    kept: list[str] = []
    total = 0
    for s in sentences:                      # keep hook + as much body as fits
        w = len(s.split())
        if total + w > max_words and kept:
            break
        kept.append(s)
        total += w
    return " ".join(kept).rstrip()
