"""Client for the public FPL API (no login).

Only the public endpoints are used. Responses can be cached on disk for local
development (set ``FPL_CACHE_DIR``); in GitHub Actions every run fetches fresh data.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://fantasy.premierleague.com/api"
USER_AGENT = "fpl-optimizer (+https://github.com/andreastegle-stack/fpl-optimizer)"


class FplApi:
    def __init__(self, cache_dir: str | os.PathLike | None = None, max_age_s: float = 3600):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        cache_dir = cache_dir or os.environ.get("FPL_CACHE_DIR")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.max_age_s = max_age_s

    def get(self, path: str) -> Any:
        path = path.strip("/") + "/"
        cache_file = None
        if self.cache_dir:
            cache_file = self.cache_dir / (path.replace("/", "_") + "json")
            if cache_file.exists() and time.time() - cache_file.stat().st_mtime < self.max_age_s:
                return json.loads(cache_file.read_text(encoding="utf-8"))
        data = self._fetch(f"{BASE_URL}/{path}")
        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _fetch(self, url: str, attempts: int = 4) -> Any:
        for attempt in range(attempts):
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    raise FileNotFoundError(url)
                # 429/5xx: back off and retry. During the "game is being updated"
                # window FPL returns 503 for a while.
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(2 ** attempt * 2)
        raise RuntimeError(f"FPL API request failed after {attempts} attempts: {url}")

    def bootstrap(self) -> dict:
        return self.get("bootstrap-static")

    def fixtures(self) -> list[dict]:
        return self.get("fixtures")

    def entry(self, entry_id: int) -> dict:
        return self.get(f"entry/{entry_id}")

    def entry_history(self, entry_id: int) -> dict:
        return self.get(f"entry/{entry_id}/history")

    def picks(self, entry_id: int, event: int) -> dict:
        return self.get(f"entry/{entry_id}/event/{event}/picks")

    def transfers(self, entry_id: int) -> list[dict]:
        return self.get(f"entry/{entry_id}/transfers")

    def element_summary(self, element_id: int) -> dict:
        return self.get(f"element-summary/{element_id}")

    def event_live(self, event: int) -> dict:
        return self.get(f"event/{event}/live")

    def classic_standings(self, league: int, page: int) -> dict:
        return self._fetch(f"{BASE_URL}/leagues-classic/{league}/standings/?page_standings={page}")

    def element_summaries(self, element_ids: list[int], workers: int = 8) -> dict[int, dict]:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = pool.map(self.element_summary, element_ids)
            return dict(zip(element_ids, results))
