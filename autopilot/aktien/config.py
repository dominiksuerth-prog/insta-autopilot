import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Logos (und die Liste nicht gefundener Logos) liegen im Repo, damit sie nicht bei jedem Lauf neu geladen werden.
INSTANCE_DIR = str(ROOT / "data" / "aktien")
# Schriften lädt der Workflow beim ersten Lauf (setup_assets) und legt sie hier ab.
FONT_DIR = str(ROOT / "data" / "aktien" / "fonts")
os.makedirs(INSTANCE_DIR, exist_ok=True)
