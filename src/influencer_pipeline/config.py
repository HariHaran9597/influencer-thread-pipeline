"""Central configuration: keys, thresholds, platform limits, paths.

Everything tweakable lives here so the eval harness can vary one knob.
Missing API keys are not an error: the pipeline degrades to deterministic
mock mode so demos, tests and CI run offline for free.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# src/influencer_pipeline/config.py -> repo root
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------- keys ----
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("GROQ_API_TOKEN") or ""
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY") or ""
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY") or ""

WRITER_MODEL = os.getenv("WRITER_MODEL", "llama-3.3-70b-versatile")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "llama-3.1-8b-instant")

FORCE_MOCK = os.getenv("MOCK_LLM", "0") == "1"

# Deployment controls. Defaults preserve the local demo, while production can
# opt into authentication, restricted CORS, and privacy-preserving traces.
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "").strip()
CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()]
RATE_LIMIT_PER_MINUTE = max(0, int(os.getenv("RATE_LIMIT_PER_MINUTE", "30")))
TRACE_RAW_CONTENT = os.getenv("TRACE_RAW_CONTENT", "0") == "1"


def mock_llm_mode() -> bool:
    """True when no usable *writer* LLM exists — the writer drives the run.
    (Google/Gemini keys only power the judge, which degrades independently.)"""
    if FORCE_MOCK:
        return True
    return not GROQ_API_KEY


def mock_search_mode() -> bool:
    return not TAVILY_API_KEY


# -------------------------------------------------------- thresholds ----
FACT_PASS_THRESHOLD = 0.9   # claim must be entailed by its cited source
MAX_FACT_RETRIES = 2        # then unverifiable claims are DROPPED, not looped forever
MAX_EDITOR_ROUNDS = 3       # writer <-> editor loop cap
EDITOR_APPROVE_SCORE = 4.0  # mean of clarity/tone/platform_fit must be >= this

# ---------------------------------------------------- platform limits ----
LINKEDIN_MAX_WORDS = 600
TWITTER_CHAR_LIMIT = 280
TWITTER_URL_COST = 23       # t.co wraps every URL to 23 chars regardless of length
INSTAGRAM_MAX_WORDS = 150
INSTAGRAM_HASHTAGS = 20
N_SOURCES = 10              # sources fetched per research round

# -------------------------------------------------------------- paths ----
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"        # search cache: reproducible evals, zero quota burn
REPORTS_DIR = ROOT / "reports"
MOCK_SOURCES_PATH = DATA_DIR / "mock_sources.json"
ADVERSARIAL_PATH = DATA_DIR / "adversarial_injections.jsonl"
TOPICS_PATH = DATA_DIR / "topics.jsonl"
TRACES_PATH = REPORTS_DIR / "traces.jsonl"

for _d in (CACHE_DIR, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
