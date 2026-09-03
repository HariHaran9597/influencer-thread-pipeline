# Eval report — influencer-thread-pipeline

**Date:** 2026-08-31 · **Topics:** 20 · **Mode:** mock (offline, deterministic) ·
**Fact/style judge:** heuristic

> Numbers below were measured by `scripts/evaluate.py`, not hand-written.
> Mock-mode runs use the deterministic offline writer/judge — rerun with API keys for live-model numbers.

## Multi-agent pipeline vs single-prompt baseline

| Config | Fact pass | Claims dropped/run | Editor reject curve | Style L/T/I (avg) | Tokens/run | Latency p95 |
|---|---|---|---|---|---|---|
| single-prompt baseline | 83.3% | – | – | 3.7 / 3.7 / 3.7 | 763 | – |
| 4-agent pipeline (x3 loops) | 100.0% | 0.0 | 100.0% -> 0.0% | 5.0 / 5.0 / 5.0 | 3,225 | 0.0s |

## Fact-check loop (pipeline)

| Metric | Value |
|---|---|
| mean claims checked/run | 10.0 |
| mean fact retries/run | 1.00 (cap 2) |
| topics needing a retry | 20/20 |

## Guardrail: prompt-injection suite

**Blocked 13/13 injections**
(100%)
· false positives on clean docs: 0
· none missed


## Reproduce

```bash
python scripts/evaluate.py --limit 20          # auto mode
python scripts/evaluate.py --mock              # offline deterministic
```

---

## LIVE partial results (2026-09-01 run, from traces.jsonl — auto-section, safe to delete)

The full live harness rerun was cut short by Groq's free-tier daily token cap
(200k TPD on qwen/qwen3.8-27b); these numbers are computed from the 26 live
runs (22 topics) recorded in `reports/traces.jsonl` before the cutoff.

| Metric | Live value (qwen3.8-27b writer, gpt-oss-20b judge, Tavily) |
|---|---|
| Fact pass | **96.3% mean** (23/26 runs at 100%, min 50%) |
| Claims checked / run | 10.5 |
| Claims dropped as unverifiable / run | 1.15 |
| Fact-check retries / run | 1.19 (cap 2) |
| Editor reject curve | v1 100% → v2 58% → v3 92% (n=6 reached v3) |
| Latency | median 157s · p95 406s (3 platforms, loops included) |
| Citation errors across all runs | 1 (caught by output guardrail) |

Rerun `python scripts/evaluate.py --limit 20` on a fresh quota day to replace
this section with the full harness table.
