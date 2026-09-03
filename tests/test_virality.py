from src.influencer_pipeline.virality import virality_node


def _state(outputs):
    return {
        "sources": [{"title": "Source", "url": "https://example.com", "snippet": "A verified 42% result.", "date": ""}],
        "outputs": outputs,
        "telemetry": [],
        "log": [],
    }


def test_virality_strategy_returns_platform_specific_angles():
    result = virality_node(_state({
        "twitter": "Here's the 42% result? [1]\n\nOne cited idea. [1]\n\nWhat would you test next?",
        "instagram": "Here's the 42% result. [1]\n\nSave this breakdown. [1]\n\nWhich part surprised you?",
    }))
    assert set(result["virality"]) == {"twitter", "instagram"}
    assert result["virality"]["twitter"]["score"] > 0
    assert "Number-led" in result["virality"]["twitter"]["angle"]
    assert "carousel" in result["virality"]["instagram"]["angle"]


def test_virality_strategy_has_safety_guardrail():
    result = virality_node(_state({"linkedin": "A broad update with no evidence."}))
    report = result["virality"]["linkedin"]
    assert report["recommendations"]
    assert "never add fake urgency" in report["guardrail"]
