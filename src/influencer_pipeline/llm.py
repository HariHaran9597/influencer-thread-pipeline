"""LLM access with graceful fallback to deterministic mock mode.

Two roles, deliberately split to avoid self-grading:
  - writer LLM  : drafts and revisions (Groq WRITER_MODEL)
  - judge LLM   : fact-check entailment + style scores. Prefers GOOGLE_API_KEY
                  (different vendor than the writer); falls back to Groq's
                  smaller JUDGE_MODEL; falls back to heuristics when keyless.

Keys are read from config dynamically (never imported by value) so BYOK runs
can swap them per request via runtime.runtime_keys().
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from . import config

_writer_llm = None
_judge_llm = None
_judge_provider = "heuristic"
_writer_warned = False


def reset() -> None:
    """Drop cached clients (called when keys change between runs)."""
    global _writer_llm, _judge_llm, _judge_provider, _writer_warned
    _writer_llm = None
    _judge_llm = None
    _judge_provider = "heuristic"
    _writer_warned = False


def _build_writer():
    global _writer_llm, _writer_warned
    if _writer_llm is None and config.GROQ_API_KEY:
        try:
            from langchain_groq import ChatGroq
            _writer_llm = ChatGroq(model=config.WRITER_MODEL, temperature=0.4)
        except Exception as exc:
            if not _writer_warned:
                print(f"[llm] live writer unavailable ({type(exc).__name__}: {exc}); using mock writer")
                _writer_warned = True
    return _writer_llm


def _build_judge():
    """Judge must be a DIFFERENT model than the writer whenever possible."""
    global _judge_llm, _judge_provider
    if _judge_llm is not None or config.mock_llm_mode():
        return _judge_llm
    if config.GOOGLE_API_KEY:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            _judge_llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)
            _judge_provider = "google"
            return _judge_llm
        except Exception:
            pass
    if config.GROQ_API_KEY:
        try:
            from langchain_groq import ChatGroq
            _judge_llm = ChatGroq(model=config.JUDGE_MODEL, temperature=0)
            _judge_provider = "groq-small"
        except Exception:
            _judge_llm = None
    return _judge_llm


def judge_provider() -> str:
    _build_judge()
    return _judge_provider


def writer_available() -> bool:
    return _build_writer() is not None


def call_llm(llm, system: str, user: str, retries: int = 3) -> tuple[str, int]:
    """Invoke a chat model; returns (text, total_tokens).

    Free tiers rate-limit (Groq ~30 req/min, token/min caps), so failures get
    exponential backoff — a 20-topic batch eval must survive 429s."""
    from langchain_core.messages import HumanMessage, SystemMessage

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            tokens = 0
            usage = getattr(resp, "usage_metadata", None)
            if usage:
                tokens = int(usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
            return resp.content, tokens
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
    raise last_exc  # type: ignore[misc]


def estimate_tokens(text: str) -> int:
    """Rough token estimate used for telemetry in mock mode (~4 chars/token)."""
    return max(1, len(text) // 4)


def parse_json_loose(text: str) -> Optional[dict]:
    """LLMs love wrapping JSON in prose/fences. Extract the first JSON object."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None
