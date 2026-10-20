"""Settings and repo paths."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "config" / "settings.json"
PENDING_PATH = ROOT / "data" / "pending_transfers.json"
OVERRIDES_PATH = ROOT / "data" / "overrides.json"
# Advice snapshots (for the scorecard). In Actions they live on the ``state`` branch,
# checked out separately, so bot commits never touch main.
ADVICE_DIR = Path(os.environ.get("FPL_ADVICE_DIR", ROOT / "history" / "advice"))
# Reviews of finished gameweeks, stored next to the advice snapshots.
REVIEW_DIR = ADVICE_DIR.parent / "review"
CACHE_DIR = ROOT / ".cache"


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
