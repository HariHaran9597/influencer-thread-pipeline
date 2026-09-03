"""CLI: python -m src.influencer_pipeline "iPhone 17 launch" --platform all"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .graph import run_pipeline
from .tracker import run_summary

LABEL = {"linkedin": "LINKEDIN POST", "twitter": "TWITTER THREAD", "instagram": "INSTAGRAM CAPTION"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="influencer-pipeline",
                                 description="Research->Write->Fact-check->Edit->Format pipeline")
    ap.add_argument("topic", help="topic for the thread")
    ap.add_argument("--platform", default="all",
                    help="all, or comma-separated: linkedin,twitter,instagram")
    ap.add_argument("--json", metavar="PATH", help="write full run state to PATH as JSON")
    ap.add_argument("--mock", action="store_true",
                    help="force offline mock mode (no API calls, deterministic)")
    args = ap.parse_args(argv)

    if args.mock:
        from . import config as _config
        _config.FORCE_MOCK = True

    platforms = None
    if args.platform != "all":
        platforms = [p.strip() for p in args.platform.split(",")]

    state = run_pipeline(args.topic, platforms)

    print("=" * 72)
    for p in state["platforms"]:
        print(f"\n--- {LABEL.get(p, p.upper())} " + "-" * (60 - len(p)))
        print(state["outputs"].get(p, ""))
    print("\n" + "=" * 72)
    summary = run_summary(state)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\nRun log:")
    for line in state.get("log", []):
        print(" ", line)

    if args.json:
        Path(args.json).write_text(
            json.dumps({k: v for k, v in state.items() if k != "telemetry"} |
                       {"telemetry": list(state.get("telemetry", []))},
            ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print(f"\nFull state -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
