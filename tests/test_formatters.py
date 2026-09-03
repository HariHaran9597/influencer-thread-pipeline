from src.influencer_pipeline.formatters import (
    format_instagram,
    format_linkedin,
    format_twitter,
    split_thread,
    tweet_length,
)

LONG_DRAFT = (
    "Here's what 5 sources actually say about solid state batteries:\n\n"
    "Toyota announced a solid state battery prototype with a 745 mile range. [1]\n\n"
    "QuantumScape shipped its first commercial samples to a top automaker last quarter. [2]\n\n"
    "Charging to 80 percent takes under 10 minutes on the new cells according to lab tests. [3]\n\n"
    "Production costs remain the largest barrier at roughly 3x conventional lithium ion packs. [4]\n\n"
    "Analysts now expect the first mass market EV with solid state cells by 2028. [5]\n\n"
    "Full breakdown with sources below."
)


def test_twitter_thread_respects_280_every_tweet():
    thread = split_thread(LONG_DRAFT)
    assert len(thread) >= 3
    assert all(tweet_length(t) <= 280 for t in thread)
    # numbered and in order
    for i, t in enumerate(thread, 1):
        assert t.startswith(f"{i}/")


def test_twitter_url_counts_as_23_chars():
    long_url = "https://example.com/" + "a" * 80
    text = f"Read the details here {long_url}"
    assert tweet_length(text) == len("Read the details here ") + 23


def test_twitter_never_splits_citation_or_word():
    thread = split_thread(LONG_DRAFT)
    for t in thread:
        body = t.split(" ", 1)[1]
        # no dangling open bracket cite (a split [n] would leave '[' at line end)
        for line in body.splitlines():
            assert not line.rstrip().endswith("[")


def test_linkedin_caps_at_600_words_and_keeps_hook():
    draft = LONG_DRAFT + " Padding sentence about nothing. " * 300
    out = format_linkedin(draft, max_words=600)
    body = out.split("\n\nSources:")[0]
    assert len(body.split()) <= 600
    assert body.startswith("Here's what")   # hook survives the trim


def test_instagram_has_exactly_20_hashtags_and_word_cap():
    titles = ["Solid State Battery Breakthrough", "EV Battery Costs Fall",
              "Toyota Battery Prototype", "QuantumScape Shipping", "Charging Speed Records"]
    out = format_instagram(LONG_DRAFT, "solid state battery EV progress", titles,
                           max_words=150, n_hashtags=20)
    caption, _, tags = out.rpartition("\n\n")
    assert len(caption.split()) <= 150
    assert len(tags.split()) == 20
    assert all(t.startswith("#") for t in tags.split())


def test_twitter_prefix_reservation_handles_tweet_ten():
    paragraph = " ".join(["abcde"] * 46)
    out = format_twitter("\n\n".join([paragraph] * 10))
    assert all(tweet_length(tweet) <= 280 for tweet in split_thread(out))


def test_instagram_pads_thin_topics_to_requested_hashtag_count():
    out = format_instagram("Short caption.", "AI", [], n_hashtags=20)
    assert len(out.rpartition("\n\n")[2].split()) == 20
