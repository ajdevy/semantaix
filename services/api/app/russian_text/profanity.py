from __future__ import annotations

from functools import lru_cache
from pathlib import Path

DEFAULT_PROFANITY_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "russian_profanity.txt"
)


@lru_cache(maxsize=8)
def load_profanity(path: str | Path | None = None) -> frozenset[str]:
    resolved = Path(path) if path is not None else DEFAULT_PROFANITY_PATH
    entries: set[str] = set()
    for line in resolved.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped.lower())
    return frozenset(entries)
