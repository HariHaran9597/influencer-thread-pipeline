from src.influencer_pipeline.fact_checker import (
    _remove_sentence,
    extract_claims,
    heuristic_entailment,
)

SNIPPET = ("Apple unveiled the iPhone 17 series powered by the new A19 chip. "
           "The base model starts at $799 and ships September 19.")


def test_extract_claims_pulls_sentence_and_index():
    draft = ("Intro line with no cite.\n\n"
             "Apple unveiled the iPhone 17 series powered by the new A19 chip. [1] "
             "Shipping begins September 19. [2]")
    claims = extract_claims(draft)
    assert ("Apple unveiled the iPhone 17 series powered by the new A19 chip.", 1) in claims
    assert ("Shipping begins September 19.", 2) in claims


def test_heuristic_entailment_verbatim_claim_scores_high():
    score, reason = heuristic_entailment(
        "Apple unveiled the iPhone 17 series powered by the new A19 chip.", SNIPPET)
    assert score == 1.0


def test_heuristic_entailment_fabricated_claim_scores_low():
    score, _ = heuristic_entailment(
        "Industry insiders confirm expectations are at an all-time high this cycle.", SNIPPET)
    assert score < 0.5


def test_number_mismatch_fails_even_when_words_match():
    score, _ = heuristic_entailment(
        "The base model starts at $899 and ships September 19.", SNIPPET)
    assert score < 0.9          # wrong price must not pass


def test_remove_sentence_strips_claim_and_citation():
    draft = "Keep this fact. [1] Remove this exact claim sentence. [2] Also keep. [3]"
    cleaned = _remove_sentence(draft, "Remove this exact claim sentence.")
    assert "Remove this exact claim" not in cleaned
    assert "[2]" not in cleaned
    assert "Keep this fact." in cleaned and "Also keep." in cleaned
