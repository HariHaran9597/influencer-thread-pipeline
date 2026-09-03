# 🧵 Influencer Thread Pipeline

**Researcher → Writer → Fact-checker → Editor ×3 → Formatter → Virality strategist → LinkedIn / Twitter / Instagram**, wired as a
[LangGraph](https://langchain-ai.github.io/langgraph/) state machine with two real feedback loops
and a measurement harness. Not "chat with a PDF": every claim carries a `[n]` citation, the
fact-checker sends unverifiable claims back (or **drops** them), the editor rejects and improves
drafts, and a guardrail layer quarantines prompt-injected sources — all of it traced and evaluated
on a 20-topic golden set.

![mode](https://img.shields.io/badge/mode-live%20%2B%20mock-blueviolet)
![fact](https://img.shields.io/badge/fact--check%20pass-96.3%25%20live%20%C2%B7%20100%25%20mock-green)
![injections](https://img.shields.io/badge/injections%20blocked-13%2F13-brightgreen)
![tests](https://img.shields.io/badge/tests-34%20passed-success)

> The repository includes a historical live snapshot plus a deterministic offline baseline.
> Mock scores validate routing and guardrail plumbing; they are not claims of real-world factual
> accuracy. Full detail and reproduction commands are in [`reports/eval.md`](reports/eval.md).

## Architecture

```
START
  └─> Researcher ── Tavily (or mock corpus, disk-cached) ──> 10 sources {title,url,snippet,date}
        └─> Guardrail[sources] ── injection scan ── quarantined sources never reach the writer
              └─> Writer ── cited drafts v1 per platform (mock v1 ships one bad claim on purpose)
                    └─> Fact-checker ── claim vs source entailment (0-1, independent judge)
                          │   fail & retries < 2 ──> back to Researcher   (loop 1)
                          │   fail & retries = 2 ──> DROP the claims      (never loop forever)
                          ▼
                    Editor ── clarity / tone / platform-fit (1-5) + critique, max 3 rounds
                          │   any reject & rounds < 3 ──> back to Writer  (loop 2)
                          ▼
                    Formatters ── LinkedIn ≤600w · Twitter 280-char numbered thread (URLs=23) · IG caption+20 hashtags
                          └─> Guardrail[outputs] ── PII redaction + citation verification
                                └─> Virality strategist ── evidence-aware hook / format / interaction score
                                      └─> Tracker ── reports/traces.jsonl (FULL state: every version,
                                               every score, every critique, tokens, latency)
```

**State is the product**: research bundle, every draft version, per-claim fact scores, editor
critiques, guardrail reports and telemetry all survive to `reports/traces.jsonl`.

## Quickstart ($0 — works offline)

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # (linux/mac: .venv/bin/pip)

# no API keys needed — deterministic mock mode:
.venv/Scripts/python -m src.influencer_pipeline "iPhone 17 launch specs and price" --mock

# web demo: 3 platform tabs + sources + fact-check + editor loop + trace
.venv/Scripts/python app.py                          # -> http://127.0.0.1:7860

# tests (34) and eval harness (20 topics, A/B vs single-prompt baseline)
.venv/Scripts/python -m pytest tests/
.venv/Scripts/python scripts/evaluate.py --mock
```

### Going live (still $0)

Copy `.env.example` → `.env` and add any of:

| Key | Powers | Free tier (verified Aug 2026) |
|---|---|---|
| `GROQ_API_KEY` | writer/editor LLM | ~30 req/min, ~14.4k req/day |
| `TAVILY_API_KEY` | web research | 1,000 credits/month |
| `GOOGLE_API_KEY` | independent judge (recommended — judge ≠ writer) | generous free quota |
| `LANGSMITH_API_KEY` | LangSmith tracing | free developer tier |

Every capability degrades independently: no Tavily → mock corpus; no Groq → deterministic mock
writer; no judge key → heuristic entailment. The demo never breaks on stage. Search results are
disk-cached (`data/cache/`) so eval re-runs are reproducible and don't burn quota.

## Measured results

**Historical live snapshot (from `reports/traces.jsonl`):**

| Metric | Value |
|---|---|
| Fact pass (claims entailed by cited source) | **96.3% mean**, 23/26 runs at 100% |
| Claims dropped as unverifiable / run | 1.15 (dropped, never published) |
| Fact-check retries / run | 1.19 (cap 2, then drop) |
| Editor reject curve | v1 100% → v2 58% → v3 92% |
| Latency (3 platforms, loops included) | median 157s · p95 406s |
| Injection suite | **13/13 blocked, 0 false positives** |

**Mock (20-topic synthetic benchmark, deterministic — from `reports/eval.md`):**

| Config | Fact pass | Editor reject curve | Style L/T/I | Tokens/run |
|---|---|---|---|---|
| single-prompt baseline | 83.3% | – | 3.7 / 3.7 / 3.7 | 763 |
| **4-agent pipeline (×3 loops)** | **100.0%** | 100% → 0% | 5.0 / 5.0 / 5.0 | 3,225 |

- Fact-check loop: 10 claims/run checked, 1.00 retries/run average, 20/20 topics needed ≥1 retry
- Guardrail suite: **13/13 injections blocked, 0 false positives** on clean docs
  ([`data/adversarial_injections.jsonl`](data/adversarial_injections.jsonl))
- 34/34 tests pass, including end-to-end graph tests that assert both loops fire

*Mock-mode numbers prove the plumbing (loops route, claims get caught/dropped, guardrails fire)
deterministically. Live-mode LLM numbers will differ — rerun `scripts/evaluate.py` with keys and
the report regenerates itself. Report measured numbers, never targets.*

## Design decisions

1. **Judge ≠ writer.** Fact-check/style judging prefers a Google key, else a *different, smaller*
   Groq model — a model grading its own output is self-grading. Heuristic fallback (word/number
   containment) keeps it honest offline, and a wrong number in an otherwise-verbatim claim fails.
2. **Loops are capped, then escalate.** Fact-check retries twice, then *drops* unverifiable claims
   (dropped-count is a reported metric). Editor loops 3 rounds max, then ships and the cap-hit is
   visible in traces.
3. **Guardrails run twice.** Injection scan happens on sources *before* the writer sees them;
   PII redaction + `[n]` citation verification happen on final outputs.
4. **Cache every search.** Reproducible evals, zero quota burn while debugging loops.
5. **Editor approval is not a mean.** Approve = mean ≥ 4.0 **and** no dimension < 3.5 — a great
   average can't hide a missing hook.

## Layout

```
src/influencer_pipeline/
  config.py  state.py  llm.py          # knobs, typed LangGraph state, provider fallbacks
  researcher.py  guardrail.py          # Tavily+cache+mock · injection/PII/citation
  writer.py  fact_checker.py  editor.py  virality.py # agents + deterministic virality strategist
  formatter.py  formatters/{linkedin,twitter,instagram}.py
  graph.py  tracker.py  __main__.py    # wiring · traces.jsonl · CLI
scripts/evaluate.py                    # 20-topic A/B harness -> reports/eval.md
app.py                                 # Gradio demo (quick local UI)
backend/main.py                        # FastAPI: /api/run NDJSON stream + BYOK + static hosting
frontend/                              # custom minimal dark UI (Vercel-deployable)
  index.html  styles.css  app.js
tests/                                 # 34 tests: formatters, guardrails, e2e graph, virality strategy
data/{topics.jsonl, mock_sources.json, adversarial_injections.jsonl, cache/}
```

## Resume bullet (fill in your live numbers)

> **Influencer Thread Pipeline — 4-agent LangGraph system with fact-check, editor, and virality strategy loops**
> Built Researcher→Writer→Fact-checker→Editor×3 over Tavily/Groq, emitting cited LinkedIn/Twitter/Instagram posts with evidence-aware viral angles; independent-model fact-checking reached __% claim entailment on a 20-topic golden set, editor rejects fell __%→__% across rounds, guardrails blocked __/13 prompt injections with 0 false positives, and the single-prompt A/B baseline shows the loop's value at __× cost — all traced to per-run audit logs.

## Deploy (both free)

Two-piece architecture: **Vercel hosts the static frontend, HF Spaces hosts the FastAPI backend**.
(Vercel's free serverless functions cap at ~60s and a live run takes 50–150s, so the pipeline
backend lives on HF Spaces' free persistent tier instead.)

### Backend → Hugging Face Spaces (FastAPI SDK)

1. Create a Space → SDK: **Docker** (or Gradio SDK w/ custom start), push the repo.
2. Add Space secrets: `GROQ_API_KEY`, `TAVILY_API_KEY` (server mode — visitors' BYOK keys are not needed).
3. `Dockerfile` = python:3.11-slim, `pip install -r requirements.txt`, `CMD uvicorn backend.main:app --host 0.0.0.0 --port 7860`.
4. Your API is at `https://<user>-<space>.hf.space` — it also serves this same frontend, so one URL works alone.

### Frontend → Vercel

1. Push repo to GitHub → Vercel "Import Project" → Framework Preset: **Other** (vercel.json already
   sets `outputDirectory: frontend`).
2. Visitors click **⚙ Settings** and paste *their own* Groq/Tavily keys (BYOK) — keys are stored in
   the visitor's localStorage and sent per request only, never persisted server-side. Without keys
   the app runs in mock mode.
3. Point the frontend at your backend: visitors enter the Space URL under Settings → Backend URL
   (or hardcode it in `frontend/app.js` before deploying).
4. When you "go live" properly, keep the server keys and hide the BYOK panel — same code path.

> Rotate any API key that has ever been pasted into a chat or committed anywhere before publishing.

## Portfolio and production notes

This repository is a portfolio-grade reference implementation of a grounded,
stateful LLM workflow. Mock-mode evaluation is intentionally deterministic and
uses a synthetic corpus; its scores validate routing, formatting, and guardrail
plumbing, not real-world factual accuracy. Live quality numbers should only be
reported with the source set, model versions, judge coverage, and run date.

For a public deployment, configure `API_AUTH_TOKEN`, `CORS_ORIGINS`,
`RATE_LIMIT_PER_MINUTE`, and leave `TRACE_RAW_CONTENT=0`. The API includes a
single-process lock and rate limit; multi-worker deployments should enforce
authentication, rate limiting, and request timeouts at a shared gateway.

The output path re-checks citations after formatting, and every writer revision
returns through fact-checking before editor approval. Stable `source_id` values
are retained in the audit state so source provenance survives retries and
source quarantine.

## Roadmap

- [ ] Re-run live-mode evaluation with a versioned, primary-source benchmark
- [ ] Optional local BGE cross-encoder for entailment scoring (`sentence-transformers`)
- [ ] LangSmith trace dashboard screenshots in README
- [ ] GIF: research → draft → fact-fail → fix → approved output
