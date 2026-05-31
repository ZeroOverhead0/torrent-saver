"""Torrent Saver — rescue dying torrents by downloading and re-seeding them.

A self-contained FastAPI app that searches indexers for *endangered* torrents
(few seeders), skips ones that already have healthy duplicate copies, and re-seeds
the survivors within a user-chosen machine budget — behind a VPN kill-switch.

Publishable standalone; also mounts into the Claude dashboard hub at /torrentsaver.
"""

__version__ = "1.0.0"
