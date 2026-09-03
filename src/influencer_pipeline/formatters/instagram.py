"""Instagram formatter: caption capped at 150 words + exactly 20 hashtags
derived from the topic and source titles (padded from a seed pool when the
topic is thin)."""
from __future__ import annotations

import re

_SENT = re.compile(r"(?<=[.!?])\s+")
_STOP = {"the", "and", "for", "with", "you", "your", "how", "what", "why", "are",
         "was", "were", "from", "that", "this", "into", "about", "all", "top",
         "new", "best", "guide", "tips"}

_SEED_POOL = ["Research", "Facts", "DeepDive", "LearnDaily", "Evidence",
              "Explained", "Trending", "MustRead", "Insights", "SmartContent",
              "Knowledge", "Analysis", "Data", "Technology", "Innovation",
              "Strategy", "Ideas", "Learning", "Updates", "Community"]


def _hashtags(topic: str, source_titles: list[str], n: int = 20) -> list[str]:
    freq: dict[str, int] = {}
    for text in [topic] + source_titles:
        for tok in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", text):
            t = tok.lower()
            if t in _STOP:
                continue
            freq[t] = freq.get(t, 0) + 1
    ranked = [t for t, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))]
    tags = [f"#{t.capitalize()}" for t in ranked[:n]]
    for seed in _SEED_POOL:                   # pad to exactly n
        if len(tags) >= n:
            break
        if f"#{seed}" not in tags:
            tags.append(f"#{seed}")
    return tags[:n]


def format_instagram(draft: str, topic: str, source_titles: list[str],
                     max_words: int = 150, n_hashtags: int = 20) -> str:
    text = draft.strip()
    if len(text.split()) > max_words:
        sentences = _SENT.split(text)
        kept, total = [], 0
        for s in sentences:
            w = len(s.split())
            if total + w > max_words and kept:
                break
            kept.append(s)
            total += w
        text = " ".join(kept).rstrip()
    # A single punctuation-free sentence can otherwise bypass the cap because
    # the greedy loop always keeps its first item.
    if len(text.split()) > max_words:
        text = " ".join(text.split()[:max_words]).rstrip()
    return f"{text}\n\n" + " ".join(_hashtags(topic, source_titles, n_hashtags))
