from app.config import MachineProfile
from app.curator import library_bytes, plan_rescues
from app.models import Candidate

GiB = 1024 ** 3


def prof(**kw):
    base = dict(key="t", label="t", max_disk_bytes=10 * GiB, reserve_bytes=0,
                max_torrents=3, max_torrent_size_bytes=5 * GiB,
                upload_limit_bytes_s=0, ratio_limit=2.0,
                seeding_time_limit_min=-1, max_connections=100)
    base.update(kw)
    return MachineProfile(**base)


def cand(ih, endanger, size=GiB, seeders=2, legal=True, redundancy=0):
    return Candidate(infohash=ih, name=f"t-{ih}", source="s", size_bytes=size,
                     seeders=seeders, legal=legal, endangerment=endanger,
                     redundancy=redundancy, rescuable=True)


def accepted(decisions):
    return [d for d in decisions if d.rescue]


def test_picks_most_endangered_first():
    cands = [cand("a" * 40, 50), cand("b" * 40, 90), cand("c" * 40, 70)]
    acc = accepted(plan_rescues(cands, 0, prof(), min_endangerment=10))
    assert [d.candidate.endangerment for d in acc] == [90, 70, 50]


def test_respects_disk_budget():
    cands = [cand(str(i) * 40, 90 - i, size=4 * GiB) for i in range(5)]
    acc = accepted(plan_rescues(cands, 0, prof(max_disk_bytes=10 * GiB), min_endangerment=10))
    assert sum(d.candidate.size_bytes for d in acc) <= 10 * GiB
    assert len(acc) == 2


def test_respects_torrent_count_cap():
    cands = [cand(str(i) * 40, 90, size=1) for i in range(10)]
    acc = accepted(plan_rescues(cands, 0, prof(max_torrents=3), min_endangerment=10))
    assert len(acc) == 3


def test_legal_only_filters_illegal():
    cands = [cand("a" * 40, 90, legal=False)]
    acc = accepted(plan_rescues(cands, 0, prof(), legal_only=True, min_endangerment=10))
    assert acc == []


def test_redundancy_blocks_rescue():
    cands = [cand("a" * 40, 90, redundancy=3)]
    acc = accepted(plan_rescues(cands, 0, prof(), max_redundancy=2, min_endangerment=10))
    assert acc == []


def test_skips_unrescuable_zero_seeders():
    c = cand("a" * 40, 100, seeders=0)
    c.rescuable = False
    acc = accepted(plan_rescues([c], 0, prof(), min_endangerment=10))
    assert acc == []


def test_webseed_zero_seeder_is_rescued():
    # 0 swarm seeders but webseed-backed (rescuable=True) -> still rescued.
    c = cand("a" * 40, 100, seeders=0)
    c.rescuable = True
    c.webseeds = ["https://archive.org/download/x"]
    acc = accepted(plan_rescues([c], 0, prof(), min_endangerment=10))
    assert len(acc) == 1


def test_unknown_size_reserves_cap():
    # Unknown-size candidates each reserve the per-torrent cap (5 GiB), so only
    # one fits in a 6 GiB budget — never over-fill on unknown sizes.
    cands = [cand("a" * 40, 90, size=0), cand("b" * 40, 80, size=0)]
    p = prof(max_disk_bytes=6 * GiB, max_torrent_size_bytes=5 * GiB)
    acc = accepted(plan_rescues(cands, 0, p, min_endangerment=10))
    assert len(acc) == 1


def test_oversized_torrent_rejected():
    cands = [cand("a" * 40, 90, size=9 * GiB)]
    acc = accepted(plan_rescues(cands, 0, prof(max_torrent_size_bytes=5 * GiB), min_endangerment=10))
    assert acc == []
