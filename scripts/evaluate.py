"""Eval harness: 20-topic golden set -> reports/eval.md comparison table.

Measures, per config (multi-agent pipeline vs single-prompt baseline):
  fact pass %, claims dropped, editor reject curve v1..v3, style score,
  tokens/run, latency p50/p95 — plus the 15-doc prompt-injection guardrail suite.

Usage:
  python scripts/evaluate.py                # 20 topics, auto (mock or live) mode
  python scripts/evaluate.py --limit 5 --mock
Judge is heuristic in mock mode; add GOOGLE_API_KEY/GROQ_API_KEY for LLM judging.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.influencer_pipeline import config                     # noqa: E402
from src.influencer_pipeline.editor import heuristic_scores    # noqa: E402
from src.influencer_pipeline.fact_checker import extract_claims, judge_entailment  # noqa: E402
from src.influencer_pipeline.formatters import (               # noqa: E402
    format_instagram,
    format_linkedin,
    format_twitter,
)
from src.influencer_pipeline.graph import run_pipeline         # noqa: E402
from src.influencer_pipeline.guardrail import detect_injection  # noqa: E402
from src.influencer_pipeline.llm import judge_provider         # noqa: E402
from src.influencer_pipeline.state import PipelineState        # noqa: E402

PLATFORMS = ("linkedin", "twitter", "instagram")
_THREAD_PREFIX = re.compile(r"^\s*\d+/\d+\s*", re.MULTILINE)


# --------------------------------------------------------- baseline ----
def _baseline_prompt(topic: str, sources: list) -> str:
    bundle = "\n".join(f"[{i}] {s['title']}\n{s['snippet']}" for i, s in enumerate(sources, 1))
    return (
        f"TOPIC: {topic}\n\nSOURCES:\n{bundle}\n\n"
        "Write three posts in one response, separated by the exact markers "
        "=== LINKEDIN ===, === TWITTER ===, === INSTAGRAM === (no hashtags on Instagram). "
        "Cite sources as [n]."
    )


def run_baseline(topic: str, sources: list, mock: bool) -> dict:
    """Single-prompt baseline: one shot, no loops, no editor, no fact-check."""
    t0 = time.perf_counter()
    tokens = 0
    if mock:
        from src.influencer_pipeline.writer import _mock_draft
        fake_state: PipelineState = {"topic": topic, "editor_round": 0, "fact_retries": 0}
        titles = [s["title"] for s in sources]
        outputs = {
            "linkedin": format_linkedin(_mock_draft(fake_state, "linkedin", sources),
                                        config.LINKEDIN_MAX_WORDS) + _footer(sources),
            "twitter": format_twitter(_mock_draft(fake_state, "twitter", sources),
                                      config.TWITTER_CHAR_LIMIT, config.TWITTER_URL_COST),
            "instagram": format_instagram(_mock_draft(fake_state, "instagram", sources),
                                          topic, titles, config.INSTAGRAM_MAX_WORDS,
                                          config.INSTAGRAM_HASHTAGS),
        }
        tokens = sum(config_estimate(t) for t in outputs.values())
    else:
        from src.influencer_pipeline.llm import _build_writer, call_llm
        text, tokens = call_llm(_build_writer(), "You are a social media writer.",
                                _baseline_prompt(topic, sources))
        outputs = _parse_sections(text, sources, topic)

    # score its claims with the same judge as the pipeline
    results = []
    for out in outputs.values():
        for claim, n in extract_claims(out):
            if 1 <= n <= len(sources):
                score, _ = judge_entailment(claim, f"{sources[n-1]['title']}. {sources[n-1]['snippet']}")
                results.append(score >= config.FACT_PASS_THRESHOLD)
    return {
        "outputs": outputs, "tokens": tokens,
        "latency_ms": (time.perf_counter() - t0) * 1000,
        "fact_pass_pct": round(100 * sum(results) / len(results), 1) if results else None,
        "claims": len(results),
    }


def config_estimate(text: str) -> int:
    return max(1, len(text) // 4)


def _parse_sections(text: str, sources: list, topic: str) -> dict:
    parts = re.split(r"===\s*(LINKEDIN|TWITTER|INSTAGRAM)\s*===?", text)
    got = {parts[i].lower(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    titles = [s["title"] for s in sources]
    out = {}
    if "linkedin" in got:
        out["linkedin"] = format_linkedin(got["linkedin"]) + _footer(sources)
    if "twitter" in got:
        out["twitter"] = format_twitter(got["twitter"])
    if "instagram" in got:
        out["instagram"] = format_instagram(got["instagram"], topic, titles)
    return out or {"linkedin": text, "twitter": text, "instagram": text}


def _footer(sources: list) -> str:
    return "\n\nSources:\n" + "\n".join(f"[{i}] {s['title']} — {s['url']}"
                                        for i, s in enumerate(sources, 1))


# ------------------------------------------------------------ style ----
def style_scores(outputs: dict, n_sources: int = 10) -> dict[str, float]:
    """1-5 per platform on the FINAL formatted output (numbering stripped)."""
    out = {}
    for p, text in outputs.items():
        clean = _THREAD_PREFIX.sub("", text)
        d = heuristic_scores(p, clean, n_sources)
        out[p] = round((d["clarity"] + d["tone"] + d["platform_fit"]) / 3, 2)
    return out


# ------------------------------------------------------------- main ----
def pct(vals: list[float]) -> str:
    return f"{statistics.mean(vals):.1f}" if vals else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="number of topics (default 20)")
    ap.add_argument("--mock", action="store_true", help="force mock mode")
    ap.add_argument("--out", default=str(config.REPORTS_DIR / "eval.md"))
    args = ap.parse_args()

    if args.mock:
        config.FORCE_MOCK = True
    mock = config.mock_llm_mode()

    topics = [json.loads(l) for l in config.TOPICS_PATH.read_text(encoding="utf-8")
              .splitlines() if l.strip()][: args.limit]

    pipe_runs = []
    from src.influencer_pipeline.tracker import run_summary
    for i, t in enumerate(topics, 1):
        t0 = time.perf_counter()
        state = run_pipeline(t["topic"])
        wall = (time.perf_counter() - t0) * 1000
        s = run_summary(state)
        s["wall_ms"] = wall
        s["style"] = style_scores(state["outputs"], len(state.get("sources", [])))
        pipe_runs.append(s)
        print(f"  [{i}/{len(topics)}] {t['topic'][:50]:<50} fact={s['fact']['pass_pct']}% "
              f"reject={s['editor']['reject_pct_by_round']}")

    base_runs = []
    for t in topics:
        from src.influencer_pipeline.researcher import fetch_sources
        sources, _ = fetch_sources(t["topic"], 0)
        b = run_baseline(t["topic"], sources, mock)
        b["style"] = style_scores(b["outputs"], len(sources))
        base_runs.append(b)

    def agg_reject(runs):
        rounds = sorted({k for r in runs for k in r["editor"]["reject_pct_by_round"]},
                        key=lambda k: int(k[1:]))
        parts = []
        for k in rounds:
            vals = [r["editor"]["reject_pct_by_round"][k] for r in runs
                    if k in r["editor"]["reject_pct_by_round"]]
            parts.append(f"{pct(vals)}%")
        return " -> ".join(parts)

    pipe_fact = [r["fact"]["pass_pct"] for r in pipe_runs if r["fact"]["pass_pct"] is not None]
    base_fact = [r["fact_pass_pct"] for r in base_runs if r["fact_pass_pct"] is not None]
    pipe_tokens = [r["tokens"] for r in pipe_runs]
    base_tokens = [r["tokens"] for r in base_runs]
    lat = sorted(r["wall_ms"] for r in pipe_runs)
    p95 = lat[max(0, int(len(lat) * 0.95) - 1)] if lat else 0

    # guardrail suite
    adv = [json.loads(l) for l in config.ADVERSARIAL_PATH.read_text(encoding="utf-8")
           .splitlines() if l.strip()]
    blocked = sum(1 for d in adv if bool(detect_injection(d["snippet"])) == d["expected_block"]
                  and d["expected_block"])
    missed = [d["id"] for d in adv
              if d["expected_block"] and not detect_injection(d["snippet"])]
    fp = [d["id"] for d in adv if not d["expected_block"] and detect_injection(d["snippet"])]

    style_p = {p: pct([r["style"][p] for r in pipe_runs]) for p in PLATFORMS}
    style_b = {p: pct([r["style"][p] for r in base_runs]) for p in PLATFORMS}

    mode = "mock (offline, deterministic)" if mock else "LIVE"
    md = f"""# Eval report — influencer-thread-pipeline

**Date:** {date.today().isoformat()} · **Topics:** {len(topics)} · **Mode:** {mode} ·
**Fact/style judge:** {judge_provider()}

> Numbers below were measured by `scripts/evaluate.py`, not hand-written.
> {'Mock-mode runs use the deterministic offline writer/judge — rerun with API keys for live-model numbers.' if mock else 'Live run.'}

## Multi-agent pipeline vs single-prompt baseline

| Config | Fact pass | Claims dropped/run | Editor reject curve | Style L/T/I (avg) | Tokens/run | Latency p95 |
|---|---|---|---|---|---|---|
| single-prompt baseline | {pct(base_fact)}% | – | – | {style_b['linkedin']} / {style_b['twitter']} / {style_b['instagram']} | {statistics.mean(base_tokens):,.0f} | – |
| 4-agent pipeline (x3 loops) | {pct(pipe_fact)}% | {statistics.mean([r['fact']['dropped'] for r in pipe_runs]):.1f} | {agg_reject(pipe_runs)} | {style_p['linkedin']} / {style_p['twitter']} / {style_p['instagram']} | {statistics.mean(pipe_tokens):,.0f} | {p95 / 1000:.1f}s |

## Fact-check loop (pipeline)

| Metric | Value |
|---|---|
| mean claims checked/run | {statistics.mean([r['fact']['claims_checked'] for r in pipe_runs]):.1f} |
| mean fact retries/run | {statistics.mean([r['fact']['retries'] for r in pipe_runs]):.2f} (cap {config.MAX_FACT_RETRIES}) |
| topics needing a retry | {sum(1 for r in pipe_runs if r['fact']['retries'] > 0)}/{len(pipe_runs)} |

## Guardrail: prompt-injection suite

**Blocked {blocked}/{sum(1 for d in adv if d['expected_block'])} injections**
({100 * blocked / max(1, sum(1 for d in adv if d['expected_block'])):.0f}%)
· false positives on clean docs: {len(fp)}
{('- missed: ' + ', '.join(missed)) if missed else '· none missed'}
{('- false positives: ' + ', '.join(fp)) if fp else ''}

## Reproduce

```bash
python scripts/evaluate.py --limit 20          # auto mode
python scripts/evaluate.py --mock              # offline deterministic
```
"""
    Path(args.out).write_text(md, encoding="utf-8")
    (config.REPORTS_DIR / "eval.json").write_text(
        json.dumps({"pipeline": pipe_runs, "baseline": base_runs,
                    "guardrail": {"blocked": blocked, "missed": missed, "false_positives": fp}},
                   ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(f"\nWrote {args.out}")
    print(md.split("## Multi-agent")[1][:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
