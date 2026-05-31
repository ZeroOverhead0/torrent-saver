from app.config import DEFAULT_PROFILE, DEFAULT_SETTINGS, PROFILES, VPN_PROVIDERS


def test_profiles_have_sane_invariants():
    for key, p in PROFILES.items():
        assert p.key == key
        assert p.max_disk_bytes > 0
        assert p.max_torrents > 0
        assert p.max_torrent_size_bytes > 0
        assert p.ratio_limit > 0
        assert p.reserve_bytes >= 0


def test_profiles_increase_in_capacity():
    order = ["potato", "small", "medium", "large", "server"]
    disks = [PROFILES[k].max_disk_bytes for k in order]
    assert disks == sorted(disks)
    counts = [PROFILES[k].max_torrents for k in order]
    assert counts == sorted(counts)


def test_default_profile_exists():
    assert DEFAULT_PROFILE in PROFILES


def test_defaults_are_safe():
    # Ships legal-only, no unattended adds, VPN required.
    assert DEFAULT_SETTINGS["legal_mode"] == "1"
    assert DEFAULT_SETTINGS["auto_rescue"] == "0"
    assert DEFAULT_SETTINGS["vpn_required"] == "1"


def test_vpn_providers_have_keys():
    for p in VPN_PROVIDERS:
        assert p["key"] and p["label"]
