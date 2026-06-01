import os
import sys
from pathlib import Path

# Make `import app...` work when pytest is run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Use a throwaway DB so any accidental settings access can't touch real data.
os.environ.setdefault("TORRENTSAVER_DB", "/tmp/torrentsaver-test.db")

# Ensure the schema exists (init is no longer a side effect of importing app.main).
from app.db import init_db  # noqa: E402
init_db()
