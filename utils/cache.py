import json
import os
import time
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "cache"
CACHE_TTL = 3600  # 1 hour


class FileCache:
    def __init__(self):
        CACHE_DIR.mkdir(exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        return CACHE_DIR / f"{safe}.json"

    def get(self, key: str):
        p = self._path(key)
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                entry = json.load(f)
            if time.time() - entry["ts"] > CACHE_TTL:
                p.unlink(missing_ok=True)
                return None
            return entry["data"]
        except Exception:
            return None

    def set(self, key: str, data):
        p = self._path(key)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "data": data}, f, ensure_ascii=False)

    def clear(self):
        for f in CACHE_DIR.glob("*.json"):
            f.unlink(missing_ok=True)
