# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — build a single self-contained Torrent Saver binary.

    pip install pyinstaller
    pyinstaller torrent-saver.spec --clean --noconfirm
    ./dist/torrent-saver            # double-click on Win/mac

Bundles the Jinja templates + static assets and force-includes every app.* and
uvicorn.* submodule (the app imports sources + scheduler loops dynamically, and
uvicorn loads its protocol/loop backends by name — both invisible to static
analysis).
"""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + [
        "uvicorn.lifespan.on",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
    ]
)

datas = [
    ("app/templates", "app/templates"),
    ("app/static", "app/static"),
]

a = Analysis(
    ["pyinstaller_entry.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="torrent-saver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
