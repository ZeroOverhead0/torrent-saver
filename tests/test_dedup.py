from app.dedup import (find_clones, is_same_content, normalise_title,
                       redundancy, title_similarity)
from app.models import Candidate


def c(infohash, name, size=0, seeders=0):
    return Candidate(infohash=infohash, name=name, source="t",
                     size_bytes=size, seeders=seeders)


def test_normalise_strips_release_noise():
    tokens = normalise_title("Ubuntu 22.04 Desktop amd64 [BluRay] x264-RARBG")
    assert "ubuntu" in tokens
    assert "x264" not in tokens
    assert "rarbg" not in tokens


def test_identical_titles_similar():
    assert title_similarity("Big Buck Bunny 1080p", "Big Buck Bunny 720p") > 0.8


def test_unrelated_titles_not_similar():
    assert title_similarity("Ubuntu Desktop", "Debian Server") < 0.4


def test_same_infohash_is_same_content():
    a = c("a" * 40, "totally different name one", size=100)
    b = c("a" * 40, "totally different name two", size=999)
    assert is_same_content(a, b)


def test_same_name_different_size_not_same():
    a = c("a" * 40, "Some Distro ISO", size=1_000_000)
    b = c("b" * 40, "Some Distro ISO", size=9_000_000)
    assert not is_same_content(a, b, size_tolerance=0.10)


def test_redundancy_counts_only_healthy_clones():
    target = c("1" * 40, "Cool Dataset 2021", size=1000, seeders=1)
    healthy = c("2" * 40, "Cool Dataset 2021", size=1000, seeders=40)
    sickly = c("3" * 40, "Cool Dataset 2021", size=1000, seeders=2)
    others = [healthy, sickly]
    assert redundancy(target, others, healthy_seeders=10) == 1
    assert len(find_clones(target, others)) == 2
