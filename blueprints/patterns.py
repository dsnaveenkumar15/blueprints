"""The pattern catalog (spec §7.2). Entries are drafts until the owner marks them reviewed."""
import json
from functools import lru_cache
from pathlib import Path

CATALOG = Path(__file__).with_name("patterns.json")


@lru_cache(maxsize=1)
def _load() -> tuple:
    return tuple(json.loads(CATALOG.read_text(encoding="utf-8")))


def load_patterns() -> list[dict]:
    return [dict(p) for p in _load()]


def pattern_ids() -> set[str]:
    return {p["id"] for p in _load()}
