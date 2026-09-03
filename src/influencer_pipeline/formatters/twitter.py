"""Twitter/X formatter: packs the draft into a numbered thread.

Counts every URL as 23 characters (t.co wrapping), never splits mid-word or
through a [n] citation token, and prefixes each tweet with "k/".
"""
from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://\S+|www\.\S+")


def tweet_length(text: str, url_cost: int = 23) -> int:
    n_urls = len(_URL_RE.findall(text))
    plain = sum(len(p) for p in _URL_RE.split(text))
    return plain + n_urls * url_cost


def _split_paragraph(paragraph: str, budget: int) -> list[str]:
    """Greedy word-packing; '[n]' stays attached to its claim word."""
    out, cur, cur_len = [], [], 0
    for word in paragraph.split():
        add = len(word) + (1 if cur else 0)
        if cur and cur_len + add > budget:
            out.append(" ".join(cur))
            cur, cur_len = [word], len(word)
        else:
            cur.append(word)
            cur_len += add
    if cur:
        out.append(" ".join(cur))
    return out


def format_twitter(draft: str, char_limit: int = 280, url_cost: int = 23) -> str:
    paragraphs = [p.strip() for p in draft.split("\n\n") if p.strip()]
    # Reserve room for a two-digit thread prefix. A fixed five-character
    # reservation breaks on tweet 10 ("10/10 " is six characters).
    body_budget = max(1, char_limit - 8)
    chunks: list[str] = []
    for p in paragraphs:
        budget = body_budget
        if tweet_length(p, url_cost) <= budget:
            chunks.append(p)
        else:
            chunks.extend(_split_paragraph(p, budget))
    if len(chunks) <= 1:
        return chunks[0] if chunks else draft.strip()
    formatted = [f"{i}/{len(chunks)} {c}" for i, c in enumerate(chunks, 1)]
    # If an unusually large thread needs a longer prefix, split again with a
    # dynamically calculated body budget until every final tweet is valid.
    prefix_budget = len(f"{len(chunks)}/{len(chunks)} ")
    if prefix_budget > char_limit - body_budget:
        budget = max(1, char_limit - prefix_budget)
        chunks = []
        for p in paragraphs:
            chunks.extend([p] if tweet_length(p, url_cost) <= budget
                          else _split_paragraph(p, budget))
        formatted = [f"{i}/{len(chunks)} {c}" for i, c in enumerate(chunks, 1)]
    return "\n\n".join(formatted)


def split_thread(draft: str, char_limit: int = 280, url_cost: int = 23) -> list[str]:
    """Same packing, returned as a list (used by tests and the UI)."""
    formatted = format_twitter(draft, char_limit, url_cost)
    return [t for t in formatted.split("\n\n") if t.strip()]
