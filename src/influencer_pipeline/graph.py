"""Graph wiring.

    START -> researcher -> guardrail_sources -> writer -> fact_checker
      fact_checker  --claim failed & retries left-->  researcher   (loop)
      fact_checker  --pass / claims dropped------->  editor
      editor        --any reject & rounds left---->  writer       (loop)
      editor        --approve / cap-------------->  formatter
      formatter -> guardrail_outputs -> virality -> tracker -> END

The two loops are the whole point: they are measured (fact pass %, reject
curve by round) rather than decorative.
"""
from __future__ import annotations

from . import config
from .config import mock_llm_mode
from .editor import editor_node
from .fact_checker import fact_checker_node
from .formatter import formatter_node
from .guardrail import guardrail_outputs_node, guardrail_sources_node
from .researcher import research_node
from .state import PipelineState
from .tracker import tracker_node
from .virality import virality_node
from .writer import PLATFORMS, writer_node


def _route_after_fact_check(state: PipelineState) -> str:
    return "researcher" if not state.get("fact_pass") else "editor"


def _route_after_editor(state: PipelineState) -> str:
    if state.get("rejected_platforms") and state.get("editor_round", 0) < config.MAX_EDITOR_ROUNDS:
        return "writer"
    return "formatter"


def build_graph():
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(PipelineState)
    g.add_node("researcher", research_node)
    g.add_node("guardrail_sources", guardrail_sources_node)
    g.add_node("writer", writer_node)
    g.add_node("fact_checker", fact_checker_node)
    g.add_node("editor", editor_node)
    g.add_node("formatter", formatter_node)
    g.add_node("guardrail_outputs", guardrail_outputs_node)
    g.add_node("virality", virality_node)
    g.add_node("tracker", tracker_node)

    g.add_edge(START, "researcher")
    g.add_edge("researcher", "guardrail_sources")
    g.add_edge("guardrail_sources", "writer")
    g.add_edge("writer", "fact_checker")
    g.add_conditional_edges("fact_checker", _route_after_fact_check,
                            {"researcher": "researcher", "editor": "editor"})
    g.add_conditional_edges("editor", _route_after_editor,
                            {"writer": "writer", "formatter": "formatter"})
    g.add_edge("formatter", "guardrail_outputs")
    g.add_edge("guardrail_outputs", "virality")
    g.add_edge("virality", "tracker")
    g.add_edge("tracker", END)
    return g.compile()


def run_pipeline(topic: str, platforms: list[str] | None = None,
                 graph=None) -> PipelineState:
    """One end-to-end run. platforms defaults to all three."""
    platforms = platforms or list(PLATFORMS)
    assert all(p in PLATFORMS for p in platforms), f"platforms must be in {PLATFORMS}"
    mock = mock_llm_mode()
    app = graph or build_graph()
    init: PipelineState = {
        "topic": topic,
        "platforms": platforms,
        "mock": mock,
        "sources": [],
        "research_round": 0,
        "drafts": {},
        "claim_results": [],
        "failed_claims": [],
        "dropped_claims": [],
        "fact_pass": False,
        "fact_retries": 0,
        "editor_critiques": {},
        "editor_round": 0,
        "rejected_platforms": [],
        "telemetry": [],
        "log": [f"[pipeline] topic='{topic}' platforms={platforms} "
                f"llm={'MOCK' if mock else 'live'} "
                f"search={'mock' if config.mock_search_mode() else 'tavily'}"],
    }
    return app.invoke(init, config={"recursion_limit": 60})
