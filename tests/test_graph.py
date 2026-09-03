"""End-to-end graph run in deterministic mock mode: both feedback loops must
actually fire (fact-check retry, editor reject -> approve) and every guardrail
must pass on the final output."""
import json

import pytest

from src.influencer_pipeline import config
from src.influencer_pipeline.graph import run_pipeline
from src.influencer_pipeline.formatters import tweet_length
from src.influencer_pipeline.tracker import run_summary


@pytest.fixture()
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TRACES_PATH", tmp_path / "traces.jsonl")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    return run_pipeline("iPhone 17 launch specs and price")


def test_pipeline_produces_all_three_outputs(run):
    assert set(run["outputs"]) == {"linkedin", "twitter", "instagram"}
    for out in run["outputs"].values():
        assert len(out) > 50


def test_fact_check_loop_fired_and_recovered(run):
    assert run["fact_retries"] == 1          # v1's fabricated claim sent it back once
    assert run["fact_pass"] is True
    assert all(r["status"] == "pass" for r in run["claim_results"])
    assert not any("insiders" in r["claim"] for r in run["claim_results"])


def test_editor_loop_rejected_then_approved(run):
    critiques = run["editor_critiques"]
    rounds = {c["round"] for cs in critiques.values() for c in cs}
    assert rounds == {1, 2}                  # rejected once, approved on revision
    r1 = [c for cs in critiques.values() for c in cs if c["round"] == 1]
    assert all(c["verdict"] == "reject" for c in r1)
    assert run["rejected_platforms"] == []   # approved by the end


def test_writer_revisions_return_through_fact_checker(run):
    nodes = run_summary(run)["nodes"]
    assert nodes.count("fact_checker") >= nodes.count("editor")


def test_state_preserves_every_draft_version(run):
    for platform, versions in run["drafts"].items():
        assert len(versions) >= 3            # v1, v2 (fact fix), v3 (editor fix)
        assert [v["version"] for v in versions] == list(range(1, len(versions) + 1))


def test_final_output_guardrails(run):
    gr = run["guardrail_outputs"]
    assert gr["citation_errors"] == []
    assert gr["pass_"] is True


def test_twitter_output_within_limit(run):
    for line in run["outputs"]["twitter"].split("\n\n"):
        assert tweet_length(line) <= 280


def test_linkedin_output_within_word_cap(run):
    body = run["outputs"]["linkedin"].split("\n\nSources:")[0]
    assert len(body.split()) <= 600


def test_instagram_output_has_20_hashtags(run):
    caption, _, tags = run["outputs"]["instagram"].rpartition("\n\n")
    assert len(tags.split()) == 20


def test_trace_written_with_full_state(run, tmp_path):
    trace_file = tmp_path / "traces.jsonl"
    lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    trace = json.loads(lines[0])
    assert trace["summary"]["fact"]["pass_pct"] == 100.0
    assert trace["full_state"]["sources"], "research bundle must be preserved"
    assert trace["full_state"]["drafts"], "draft versions must be preserved"
    assert all(s["source_id"] for s in trace["full_state"]["sources"])
