"""FastAPI backend for the custom web frontend.

POST /api/run  -> NDJSON stream: {"type":"step","node":...,"log":[...]} per
graph node, then {"type":"result", ...} with the full audit state. BYOK:
request may carry the caller's own Groq/Tavily keys (demo mode); server keys
(.env / Space secrets) always win when present. Keys are used in-memory only —
never logged, never traced, never stored.

Run:  uvicorn backend.main:app --port 8000
Serves the frontend too, so this single process is deployable as one HF Space.
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse   # noqa: E402
from fastapi.staticfiles import StaticFiles       # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402

from src.influencer_pipeline import config        # noqa: E402
from src.influencer_pipeline.graph import build_graph, PLATFORMS  # noqa: E402
from src.influencer_pipeline.runtime import runtime_keys  # noqa: E402
from src.influencer_pipeline.tracker import run_summary  # noqa: E402

app = FastAPI(title="Influencer Thread Pipeline API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"],
                   allow_headers=["*"])          # configure CORS_ORIGINS for public deployments

_run_lock = threading.Lock()                     # BYOK swaps process-global keys: one run at a time
_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}


class RunRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=200)
    platforms: list[str] = Field(default_factory=lambda: list(PLATFORMS), max_length=3)
    groq_key: str | None = None                  # visitor's own keys (BYOK demo mode)
    tavily_key: str | None = None

    @field_validator("topic")
    @classmethod
    def topic_must_contain_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("topic must contain at least 3 non-whitespace characters")
        return value


def _authorize(request: Request, authorization: str | None, x_api_key: str | None) -> None:
    """Optional bearer/API-key auth plus a small in-process abuse limit.

    For multiple production workers, put this policy at the gateway or use a
    shared rate-limit store; this process-local guard is intentionally only a
    safe baseline for a single Space/container.
    """
    if config.API_AUTH_TOKEN:
        supplied = x_api_key or ""
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not secrets.compare_digest(supplied, config.API_AUTH_TOKEN):
            raise HTTPException(status_code=401, detail="authentication required")

    limit = config.RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return
    host = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _rate_lock:
        recent = [t for t in _rate_hits.get(host, []) if now - t < 60]
        if len(recent) >= limit:
            _rate_hits[host] = recent
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        recent.append(now)
        _rate_hits[host] = recent


def _mode() -> dict:
    return {
        "llm": "live" if not config.mock_llm_mode() else "mock",  # effective mode, not key presence
        "search": "tavily" if config.TAVILY_API_KEY else "mock",
        "writer_model": config.WRITER_MODEL if not config.mock_llm_mode() else None,
    }


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, **_mode()}


@app.post("/api/run")
def run(req: RunRequest, request: Request,
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None)) -> StreamingResponse:
    _authorize(request, authorization, x_api_key)
    def event_stream():
        with _run_lock, runtime_keys(req.groq_key, req.tavily_key):
            yield json.dumps({"type": "mode", **_mode()}) + "\n"
            graph = build_graph()
            init = {
                "topic": req.topic.strip(),
                "platforms": [p for p in req.platforms if p in PLATFORMS] or list(PLATFORMS),
                "mock": config.mock_llm_mode(),
                "sources": [], "research_round": 0, "drafts": {},
                "claim_results": [], "failed_claims": [], "dropped_claims": [],
                "fact_pass": False, "fact_retries": 0,
                "editor_critiques": {}, "editor_round": 0, "rejected_platforms": [],
                "telemetry": [],
                "log": [f"[pipeline] topic='{req.topic.strip()}' "
                        f"llm={'MOCK' if config.mock_llm_mode() else 'live'} "
                        f"search={'mock' if config.mock_search_mode() else 'tavily'}"],
            }
            state: dict = dict(init)
            for update in graph.stream(init, {"recursion_limit": 60}):
                for node, delta in update.items():
                    for k, v in delta.items():
                        # node deltas carry APPEND-only lists (log/telemetry); a
                        # plain dict.update would clobber the accumulated trail
                        if k in ("telemetry", "log") and isinstance(v, list):
                            state[k] = list(state.get(k, [])) + list(v)
                        else:
                            state[k] = v
                    if node != "tracker":
                        yield json.dumps({
                            "type": "step", "node": node,
                            "log": delta.get("log", []),
                            "telemetry": delta.get("telemetry", []),
                        }, ensure_ascii=False) + "\n"
            summary = run_summary(state)  # type: ignore[arg-type]
            yield json.dumps({
                "type": "result",
                "summary": summary,
                "outputs": state.get("outputs", {}),
                "sources": state.get("sources", []),
                "claim_results": state.get("claim_results", []),
                "dropped_claims": state.get("dropped_claims", []),
                "editor_critiques": state.get("editor_critiques", {}),
                "guardrail_sources": state.get("guardrail_sources", {}),
                "guardrail_outputs": state.get("guardrail_outputs", {}),
                "virality": state.get("virality", {}),
                "log": state.get("log", []),
            }, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


# Static frontend last: /api/* routes above win; everything else is the SPA.
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
