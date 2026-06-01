"""Console entry point for a standalone Torrent Saver install.

Installed as the ``torrent-saver`` command (see pyproject.toml) and also runnable
as ``python -m app``. Starts the web UI with uvicorn and opens a browser.

The Claude dashboard hub does NOT use this — it spawns ``uvicorn app.main:app``
directly with ``--root-path /torrentsaver``. This entry point is for everyone
else: a single command (or a PyInstaller binary) that just works.
"""
from __future__ import annotations

import argparse
import os
import threading
import time
import webbrowser


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="torrent-saver",
        description="Rescue dying torrents — local web UI.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                        help="interface to bind (default 127.0.0.1, loopback only)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8780")),
                        help="port to serve on (default 8780)")
    parser.add_argument("--root-path", default=os.environ.get("ROOT_PATH", ""),
                        help="URL prefix when behind a reverse proxy (default none)")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser window on start")
    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}{args.root_path}/"

    open_browser = not args.no_browser and os.environ.get("TORRENTSAVER_NO_BROWSER") != "1"
    if open_browser:
        def _open():
            time.sleep(1.5)                       # give uvicorn a moment to bind
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    print(f"\n  Torrent Saver — open {url}\n  (press Ctrl-C to stop)\n")

    # Import lazily so `--help` is instant and PyInstaller analysis is clean.
    import uvicorn
    from app.main import app as asgi_app

    # Pass the app object (not an import string) so this works identically under
    # a PyInstaller binary, where import-string reloading would fail.
    uvicorn.run(asgi_app, host=args.host, port=args.port,
                root_path=args.root_path, log_level="info")


if __name__ == "__main__":
    main()
