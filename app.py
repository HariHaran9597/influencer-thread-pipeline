"""Gradio demo: Topic -> Research -> Draft -> Fact-check -> Editor -> 3 platforms.

Run:  python app.py   ->  http://127.0.0.1:7860
Works offline in mock mode (no keys needed); auto-upgrades to live Tavily/Groq
when keys are present in .env.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import gradio as gr  # noqa: E402

from src.influencer_pipeline import config                    # noqa: E402
from src.influencer_pipeline.graph import run_pipeline        # noqa: E402
from src.influencer_pipeline.llm import judge_provider       # noqa: E402
from src.influencer_pipeline.tracker import run_summary      # noqa: E402

MODE_BADGE = ("🟠 MOCK MODE (deterministic, offline)" if config.mock_llm_mode()
              else "🟢 LIVE MODE")
SEARCH_BADGE = "Tavily" if config.TAVILY_API_KEY else "mock corpus"
JUDGE_BADGE = judge_provider()


def generate(topic: str, platforms: list[str]):
    if not topic.strip():
        raise gr.Error("Enter a topic first — e.g. 'iPhone 17 launch' or 'GATE 2026 syllabus'")
    platforms = [p for p in platforms if p] or ["linkedin", "twitter", "instagram"]
    state = run_pipeline(topic.strip(), platforms)
    summary = run_summary(state)

    linkedin = state["outputs"].get("linkedin", "—")
    twitter = state["outputs"].get("twitter", "—")
    instagram = state["outputs"].get("instagram", "—")

    sources = [[i, s["title"], s["url"], s["date"] or "n.d."]
               for i, s in enumerate(state.get("sources", []), 1)]
    facts = [[r["status"], f"[{r['citation']}]", round(r["score"], 2),
              r["claim"], r["reason"][:90]]
             for r in state.get("claim_results", [])]
    edits = [[c["platform"], c["round"], c["clarity"], c["tone"], c["platform_fit"],
              c["verdict"], c["critique"]]
             for cs in state.get("editor_critiques", {}).values() for c in cs]
    log = "\n".join(state.get("log", []))
    metrics = json.dumps(
        {k: summary[k] for k in ("fact", "editor", "guardrail", "versions",
                                 "latency_ms", "tokens")},
        indent=2, ensure_ascii=False)
    return linkedin, twitter, instagram, sources, facts, edits, log, metrics


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Influencer Thread Pipeline") as demo:
        gr.Markdown(
            f"# 🧵 Influencer Thread Pipeline\n"
            f"**Researcher → Writer → Fact-checker → Editor ×3 → LinkedIn / Twitter / Instagram**\n\n"
            f"`LLM:` {MODE_BADGE} · `search:` {SEARCH_BADGE} · `judge:` {JUDGE_BADGE} · "
            f"every claim cites a source; unverifiable claims get dropped, not published.")
        with gr.Row():
            topic = gr.Textbox(label="Topic", placeholder="iPhone 17 launch specs and price",
                               scale=3)
            platforms = gr.CheckboxGroup(choices=["linkedin", "twitter", "instagram"],
                                         value=["linkedin", "twitter", "instagram"],
                                         label="Platforms", scale=1)
            btn = gr.Button("Generate", variant="primary", scale=1)
        with gr.Tabs():
            with gr.Tab("LinkedIn"):
                out_li = gr.Textbox(label="Post (600w cap, cited, sources footer)", lines=18)
            with gr.Tab("Twitter"):
                out_tw = gr.Textbox(label="Thread (280-char tweets, numbered)", lines=18)
            with gr.Tab("Instagram"):
                out_ig = gr.Textbox(label="Caption (150w + 20 hashtags)", lines=18)
            with gr.Tab("Sources"):
                src_tbl = gr.Dataframe(headers=["#", "Title", "URL", "Date"],
                                       label="Research bundle (post-guardrail)",
                                       interactive=False)
            with gr.Tab("Fact-check"):
                fact_tbl = gr.Dataframe(
                    headers=["Status", "Cite", "Score", "Claim", "Reason"],
                    label="Claim-vs-source entailment (pass ≥ 0.9)", interactive=False)
            with gr.Tab("Editor"):
                edit_tbl = gr.Dataframe(
                    headers=["Platform", "Round", "Clarity", "Tone", "Fit", "Verdict", "Critique"],
                    label="Writer ↔ Editor loop (max 3 rounds)", interactive=False)
            with gr.Tab("Trace"):
                run_log = gr.Textbox(label="Run log", lines=14, interactive=False)
                run_metrics = gr.Textbox(label="Metrics (written to reports/traces.jsonl)",
                                         lines=10, interactive=False)
        btn.click(generate, inputs=[topic, platforms],
                  outputs=[out_li, out_tw, out_ig, src_tbl, fact_tbl, edit_tbl,
                           run_log, run_metrics])
    return demo


if __name__ == "__main__":
    build_ui().queue().launch()
