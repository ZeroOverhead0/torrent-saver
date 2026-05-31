from app.resurrect import _query_terms


def test_query_terms_picks_distinctive_tokens():
    q = _query_terms("The Great Adventure 1080p x264-RARBG").split()
    assert "great" in q
    assert "adventure" in q
    assert "the" not in q          # stopword dropped
    assert "x264" not in q         # release-noise dropped


def test_query_terms_empty_for_noise_only():
    assert _query_terms("1080p x264 BluRay RARBG") == ""
