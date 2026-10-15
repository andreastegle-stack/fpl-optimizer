"""Settings and repo paths."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "config" / "settings.json"
PENDING_PATH = ROOT / "data" / "pending_transfers.json"
OVERRIDES_PATH = ROOT / "data" / "overrides.json"
ADVICE_DIR = ROOT / "history" / "advice"
CACHE_DIR = ROOT / ".cache"


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
