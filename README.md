# 💾 Torrent Saver

**Rescue dying torrents.** Torrent Saver hunts for torrents on the brink of
extinction — the ones down to a seeder or two — checks that nobody else is
already holding a healthy copy, and re-seeds the survivors within a budget you
choose, behind a VPN kill-switch. It drives [qBittorrent](https://www.qbittorrent.org/)
through its Web API and runs as a clean little web app.

> Digital preservation, automated. A huge amount of human knowledge — public-domain
> books, research datasets, old software, niche films — lives on torrents that
> quietly die when their last seeder goes offline. Torrent Saver helps you keep
> the long tail alive without babysitting it.

---

## ✨ What it does

- **Finds endangered torrents.** Scans sources for low-seeder torrents and scores
  each one's *endangerment* (0–100) from live tracker data: fewer seeders + people
  still waiting (leechers) = higher priority.
- **Skips the clones.** Won't waste your disk on something that already has several
  healthy duplicate copies. Title-normalising + size-matching dedup (with an
  optional LLM tie-breaker) measures *redundancy* and filters it out.
- **Respects your machine.** Pick a profile — Potato / Small / Medium / Large /
  Server, or fully custom — that caps disk usage, torrent count, per-torrent size,
  upload rate and share ratio. When the library fills up, the **eviction** engine
  drops the *least* endangered torrents (the ones that have recovered and are safe
  in other hands) to make room.
- **Legal-only by default.** Ships rescuing only whitelisted legal sources
  (Internet Archive, LinuxTracker). Other sources are opt-in.
- **VPN kill-switch.** Won't seed unrestricted content unless a VPN tunnel is up,
  qBittorrent is bound to it (so traffic dies if the tunnel drops), and no IP leak
  is detected. Fails closed.
- **Hardened against abuse.** Locks down qBittorrent (auth, CSRF/clickjacking
  protection, anonymous mode, no UPnP hole-punching) and caps ratio / seeding-time
  / connections so a hostile swarm can't force you to bleed bandwidth forever.
  Validates every magnet/.torrent before adding it.
- **Demand-seeding mode.** Got a fat upload pipe? Flip it on and Torrent Saver will
  *also* seed the most in-demand healthy torrents (lots of leechers waiting) so your
  bandwidth does the most good — same VPN gate applies.

## 🚀 Quick start

**Already running qBittorrent?** Any option below auto-detects it — just click
**Auto-detect** on the Settings page (or it happens on first launch).

### Option A — Download & run (no Python, no setup)

Grab the binary for your OS from [**Releases**](https://github.com/ZeroOverhead0/torrent-saver/releases),
then run it:

| OS | Run |
|----|-----|
| **Windows** | double-click `torrent-saver.exe` |
| **macOS** | `./torrent-saver` (right-click → Open the first time) |
| **Linux** | `chmod +x torrent-saver && ./torrent-saver` |

It opens <http://127.0.0.1:8780> in your browser. Click **Auto-detect** to find
your qBittorrent. Data is stored in your OS's app-data dir (no clutter next to
the binary).

### Option B — pipx / pip (you have Python ≥ 3.9)

```bash
pipx install torrent-saver     # or: pip install torrent-saver
torrent-saver                  # serves http://127.0.0.1:8780, opens a browser
```

### Option C — Docker (bundles qBittorrent for you)

```bash
git clone https://github.com/ZeroOverhead0/torrent-saver.git
cd torrent-saver && docker compose up -d
```

- Torrent Saver → <http://localhost:8080>, bundled qBittorrent → <http://localhost:8081>
  (first-run password in `docker compose logs qbittorrent`).

### Option D — From source

```bash
git clone https://github.com/ZeroOverhead0/torrent-saver.git
cd torrent-saver
./install.sh && ./run.sh       # or: pip install -e . && torrent-saver
```

### Option E — Claude dashboard hub

Wired in via `dashboard/services.py` (port **18780**), reverse-proxied at
**`/torrentsaver`**. Run `./install.sh` once and restart the hub.

### Build the binary yourself

```bash
pip install -e ".[build]"
pyinstaller torrent-saver.spec --clean --noconfirm   # → dist/torrent-saver
```

## ⚙️ How it works

```
 sources ──▶ enrich ──▶ dedup ──▶ score ──▶ curate ──▶ qBittorrent ──▶ monitor ──▶ evict
 (find)     (.torrent   (drop    (endanger  (fit the    (add + seed)   (live      (free space
            + scrape)   clones)  -ment)     budget)                    stats)     when full)
```

1. **Discover** — each enabled source returns candidates.
2. **Enrich** — for candidates that only carry a `.torrent`/identifier, download the
   metadata to read the real infohash/size, then *scrape the trackers* (HTTP BEP 48 /
   UDP BEP 15) for live seeder/leecher counts.
3. **Dedup** — compute how many *healthy* duplicates exist; high redundancy = skip.
4. **Score** — endangerment from seeders (exponential), demand (leechers), redundancy.
5. **Curate** — greedily pick the most endangered that fit the disk/count/size budget.
6. **Seed** — add to qBittorrent with hardened share limits (auto-rescue) or queue for
   your review.
7. **Monitor & evict** — refresh live stats; when over budget, evict the least
   endangered first.

### Sources

| Source | Legal | Notes |
|--------|:-----:|-------|
| **Internet Archive** | ✅ | Flagship. Millions of public-domain items; seeders scraped from archive.org trackers. |
| **LinuxTracker** | ✅ | Linux distro ISOs; old releases are often nearly dead. |
| **Prowlarr** | ⚠️ | *Your* indexers via the Prowlarr API. Off by default; under legal-only mode contributes nothing unless you assert your indexers are legal. |
| **Manual watchlist** | — | Paste a magnet / `.torrent` URL / infohash to rescue something specific. |
| **Academic Torrents** | ✅ | Ideal mission fit but fronted by a browser challenge, so it can't be auto-listed — add specific datasets via the watchlist. |

## 🔒 VPN & safety

The VPN gate is **fail-closed**: legal content can seed openly, but anything else
needs a verified tunnel + engaged kill-switch + no detected leak. Set your real ISP
IP as the *baseline* so leak detection can compare. Torrent Saver binds qBittorrent
to the tunnel interface — if the VPN drops, the interface vanishes and qBittorrent
can't send a byte.

**Honest scope:** Torrent Saver verifies the tunnel exists, carries the egress IP, and
that qBittorrent is bound to it. It can't prove from the outside that no single peer
connection ever escaped — so it errs on the side of refusing to seed. Use a provider
with a real kill-switch (or the gluetun container in `docker-compose.yml`) for
defence in depth.

## ⚖️ Legal

This tool downloads and re-seeds whatever **you** configure it to. It defaults to
legal, openly-licensed content and makes you opt in to anything else. **You are
responsible for what you seed and for complying with the laws of your jurisdiction.**
The authors provide this software for digital-preservation and lawful uses only and
accept no liability for misuse. See [LICENSE](LICENSE).

## 🛠️ Configuration

Everything is tunable from the web UI (stored in SQLite, no config file required).
For headless first-run bootstrap you can drop a `config.toml` (see
[`config.example.toml`](config.example.toml)) or set `QBIT_URL` / `QBIT_USERNAME` /
`QBIT_PASSWORD` env vars.

## 🧪 Development

```bash
./install.sh
.venv/bin/pip install pytest
.venv/bin/pytest -q          # pure-logic tests: scoring, dedup, curator, eviction
```

No libtorrent / native build: bencode, magnet parsing and tracker scraping are
pure-Python stdlib (`app/bencode.py`, `app/scrape.py`).

## 📋 Requirements

- Python 3.9+
- A running qBittorrent with the Web UI enabled (or use the bundled Docker one)
- Optional: a VPN with WireGuard/OpenVPN, Prowlarr, the Ollama broker (LLM dedup)

## License

MIT — see [LICENSE](LICENSE).
