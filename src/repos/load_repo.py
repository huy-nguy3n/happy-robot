import json
import os
from functools import lru_cache
from typing import List, Dict


@lru_cache(maxsize=1)
def _load_json(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


class LoadRepository:
    """Repository for reading demo loads from local JSON."""

    def __init__(self, path: str | None = None) -> None:
        # default path: <project_root>/src/data/fake_loads.json
        default_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "fake_loads.json")
        self.path = path or default_path

    def list(self) -> List[Dict]:
        return _load_json(self.path)
