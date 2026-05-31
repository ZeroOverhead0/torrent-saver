from app.endangerment import (endangerment_band, endangerment_score,
                              is_endangered, is_rescuable)


def test_zero_seeders_is_maximally_endangered():
    assert endangerment_score(0) == 100.0


def test_score_decreases_with_more_seeders():
    s1 = endangerment_score(1)
    s3 = endangerment_score(3)
    s10 = endangerment_score(10)
    assert s1 > s3 > s10
    assert s1 > 80


def test_demand_boosts_score():
    quiet = endangerment_score(2, leechers=0)
    wanted = endangerment_score(2, leechers=50)
    assert wanted > quiet


def test_redundancy_lowers_score():
    alone = endangerment_score(2, leechers=5, redundancy=0)
    crowded = endangerment_score(2, leechers=5, redundancy=3)
    assert crowded < alone
    assert crowded == 0.0 or crowded < alone


def test_score_is_bounded():
    for seeders in range(0, 50):
        for leechers in (0, 5, 500):
            v = endangerment_score(seeders, leechers)
            assert 0.0 <= v <= 100.0


def test_band_thresholds():
    assert endangerment_band(95) == "critical"
    assert endangerment_band(65) == "high"
    assert endangerment_band(45) == "moderate"
    assert endangerment_band(25) == "low"
    assert endangerment_band(5) == "safe"


def test_endangered_and_rescuable_flags():
    assert is_endangered(3, max_seeders_endangered=5)
    assert not is_endangered(9, max_seeders_endangered=5)
    assert is_rescuable(1)
    assert not is_rescuable(0)


def test_webseed_makes_zero_seeder_rescuable():
    # A 0-swarm-seeder torrent backed by a webseed (e.g. Internet Archive) is
    # still downloadable, and is the canonical rescue case.
    assert is_rescuable(0, has_webseed=True)
    assert not is_rescuable(0, has_webseed=False)
    assert endangerment_score(0) == 100.0


def test_truly_dead_only_rescuable_in_include_dead_mode():
    # 0 seeders + no webseed = truly dead: only attempted when the user opts in.
    assert not is_rescuable(0, has_webseed=False, include_dead=False)
    assert is_rescuable(0, has_webseed=False, include_dead=True)
    assert is_rescuable(2, include_dead=False)        # live seeder always fine


def test_download_history_boosts_dying_torrent():
    # "was popular, now dying" outranks "nobody ever wanted it" at equal seeders.
    no_history = endangerment_score(3, leechers=0, completed=0)
    much_history = endangerment_score(3, leechers=0, completed=10000)
    assert much_history > no_history
