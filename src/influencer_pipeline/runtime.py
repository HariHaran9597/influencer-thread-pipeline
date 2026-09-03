"""Per-run key overrides: the BYOK (bring-your-own-API-key) demo mode.

A visitor's keys live only in their browser and are passed per request; this
context manager swaps them into the process-global config for exactly one run,
then restores the server's own keys. The FastAPI layer serializes runs with a
lock so two visitors' keys can never interleave.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from . import config, llm

_KEYS = ("GROQ_API_KEY", "TAVILY_API_KEY", "MOCK_LLM")


_ATTRS = ("GROQ_API_KEY", "TAVILY_API_KEY", "FORCE_MOCK")


@contextmanager
def runtime_keys(groq_key: str | None = None, tavily_key: str | None = None):
    saved_cfg = {k: getattr(config, k) for k in _ATTRS}
    saved_env = {k: os.environ.get(k) for k in _KEYS}
    try:
        if groq_key:
            config.GROQ_API_KEY = groq_key.strip()
            os.environ["GROQ_API_KEY"] = groq_key.strip()
        if tavily_key:
            config.TAVILY_API_KEY = tavily_key.strip()
            os.environ["TAVILY_API_KEY"] = tavily_key.strip()
        llm.reset()          # drop cached clients bound to the previous keys
        yield
    finally:
        for k, v in saved_cfg.items():
            setattr(config, k, v)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        llm.reset()
