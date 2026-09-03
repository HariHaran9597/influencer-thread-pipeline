"""Formatter node: approved draft -> platform-native output + sources footer."""
from __future__ import annotations

import time

from . import config
from .formatters import format_instagram, format_linkedin, format_twitter
from .state import PipelineState
from .tracker import telem


def sources_footer(sources: list) -> str:
    lines = ["", "Sources:"]
    lines += [f"[{i}] {s['title']} — {s['url']}" for i, s in enumerate(sources, 1)]
    return "\n".join(lines)


def formatter_node(state: PipelineState) -> dict:
    t0 = time.perf_counter()
    sources = state.get("sources", [])
    titles = [s["title"] for s in sources]
    outputs: dict[str, str] = {}

    for platform in state["platforms"]:
        versions = state.get("drafts", {}).get(platform, [])
        draft = versions[-1]["text"] if versions else ""
        if platform == "linkedin":
            out = format_linkedin(draft, config.LINKEDIN_MAX_WORDS)
        elif platform == "twitter":
            out = format_twitter(draft, config.TWITTER_CHAR_LIMIT, config.TWITTER_URL_COST)
        else:
            out = format_instagram(draft, state["topic"], titles,
                                   config.INSTAGRAM_MAX_WORDS, config.INSTAGRAM_HASHTAGS)
        outputs[platform] = out

    footer = sources_footer(sources)
    if "linkedin" in outputs:
        outputs["linkedin"] += footer

    return {
        "outputs": outputs,
        "sources_footer": footer,
        "telemetry": [telem("formatter", t0, detail=", ".join(outputs))],
        "log": [f"[formatter] produced {', '.join(f'{p}: {len(t)} chars' for p, t in outputs.items())}"],
    }
