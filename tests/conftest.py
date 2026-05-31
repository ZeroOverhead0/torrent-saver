import os
import sys
from pathlib import Path

# Make `import app...` work when pytest is run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Use a throwaway DB so any accidental settings access can't touch real data.
os.environ.setdefault("TORRENTSAVER_DB", "/tmp/torrentsaver-test.db")
