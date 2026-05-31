import base64
import hashlib

from app import bencode
from app.scrape import build_magnet, parse_magnet


def test_bencode_roundtrip():
    value = {b"a": 1, b"list": [b"x", b"y", 3], b"d": {b"k": b"v"}}
    assert bencode.decode(bencode.encode(value)) == value


def test_infohash_from_torrent():
    info = {b"name": b"file.bin", b"length": 12345,
            b"piece length": 16384, b"pieces": b"\x00" * 20}
    meta = {b"announce": b"udp://tracker.example:1337/announce", b"info": info}
    data = bencode.encode(meta)
    expected = hashlib.sha1(bencode.encode(info)).hexdigest().lower()
    assert bencode.infohash_from_torrent(data) == expected
    parsed = bencode.info_from_torrent(data)
    assert parsed == {"name": "file.bin", "size_bytes": 12345}


def test_info_from_multifile_torrent():
    info = {b"name": b"folder",
            b"files": [{b"length": 100, b"path": [b"a"]},
                       {b"length": 250, b"path": [b"b"]}],
            b"piece length": 16384, b"pieces": b"\x00" * 20}
    data = bencode.encode({b"info": info})
    assert bencode.info_from_torrent(data)["size_bytes"] == 350


def test_parse_magnet_hex():
    ih = "abcdef0123456789abcdef0123456789abcdef01"
    m = f"magnet:?xt=urn:btih:{ih}&dn=Cool+Thing&tr=udp://t.example:1337/announce"
    parsed = parse_magnet(m)
    assert parsed["infohash"] == ih
    assert parsed["name"] == "Cool Thing"
    assert parsed["trackers"] == ["udp://t.example:1337/announce"]


def test_parse_magnet_base32():
    raw = bytes.fromhex("ab" * 20)
    b32 = base64.b32encode(raw).decode()
    parsed = parse_magnet(f"magnet:?xt=urn:btih:{b32}")
    assert parsed["infohash"] == "ab" * 20


def test_build_magnet_roundtrips():
    ih = "ab" * 20
    m = build_magnet(ih, "Name Here", ["udp://t:1/announce"])
    parsed = parse_magnet(m)
    assert parsed["infohash"] == ih
    assert parsed["name"] == "Name Here"
