from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DEFAULT_SLANG_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "russian_slang.json"
)


@lru_cache(maxsize=8)
def load_slang(path: str | Path | None = None) -> dict[str, str]:
    resolved = Path(path) if path is not None else DEFAULT_SLANG_PATH
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    return {key.lower(): value for key, value in raw.items()}
