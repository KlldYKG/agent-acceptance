# Make the src-layout package importable without an editable install
# (local dev / CI installs via `pip install -e .`; this fallback covers
# environments where the build backend cannot be fetched).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
