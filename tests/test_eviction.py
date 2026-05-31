from app.config import MachineProfile
from app.eviction import plan_evictions
from app.models import TrackedTorrent

GiB = 1024 ** 3


def prof(**kw):
    base = dict(key="t", label="t", max_disk_bytes=10 * GiB, reserve_bytes=0,
                max_torrents=100, max_torrent_size_bytes=50 * GiB,
                upload_limit_bytes_s=0, ratio_limit=2.0,
                seeding_time_limit_min=-1, max_connections=100)
    base.update(kw)
    return MachineProfile(**base)


def t(ih, size, seeders, leechers=0):
    return TrackedTorrent(infohash=ih, name=f"t-{ih}", size_bytes=size,
                          seeders=seeders, leechers=leechers)


def victims(decisions):
    return {d.infohash for d in decisions if d.evict}


def test_no_eviction_under_budget():
    tracked = [t("a" * 40, 1 * GiB, 1)]
    assert plan_evictions(tracked, prof()) == []


def test_evicts_recovered_first():
    # Over budget by ~2 GiB. The recovered (many-seeder) torrent should go first.
    tracked = [
        t("a" * 40, 6 * GiB, 1),     # critically endangered — keep
        t("b" * 40, 6 * GiB, 200),   # recovered — evict
    ]
    v = victims(plan_evictions(tracked, prof(max_disk_bytes=8 * GiB)))
    assert "b" * 40 in v
    assert "a" * 40 not in v


def test_evicts_enough_to_get_under_budget():
    tracked = [t(str(i) * 40, 3 * GiB, 100) for i in range(5)]   # 15 GiB total
    decisions = plan_evictions(tracked, prof(max_disk_bytes=8 * GiB))
    freed = sum(d.freed_bytes for d in decisions if d.evict)
    assert freed >= (15 - 8) * GiB


def test_over_count_cap_triggers_eviction():
    tracked = [t(str(i) * 40, 1, 50) for i in range(6)]
    v = victims(plan_evictions(tracked, prof(max_torrents=4)))
    assert len(v) == 2
