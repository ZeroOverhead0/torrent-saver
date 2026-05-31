import tempfile
from pathlib import Path

from app import ark


def test_archive_list_and_count():
    ark.ARK_DIR = Path(tempfile.mkdtemp())
    path = ark.archive_metadata({
        "infohash": "AB" * 20, "name": "Rare Thing", "source": "internet_archive",
        "size_bytes": 123, "trackers": ["udp://t:1/announce"],
        "webseeds": ["http://w/x"], "legal": True, "endangerment": 100,
    }, fetch_torrent=False)
    assert path
    assert ark.is_archived("ab" * 20)          # case-insensitive
    items = ark.list_ark()
    assert len(items) == 1
    assert items[0]["name"] == "Rare Thing"
    assert items[0]["has_torrent"] is False
    assert ark.count_ark() == 1


def test_archive_requires_infohash():
    ark.ARK_DIR = Path(tempfile.mkdtemp())
    assert ark.archive_metadata({"name": "no hash"}) is None
    assert ark.count_ark() == 0
