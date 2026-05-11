from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from services.api.app.russian_text import get_russian_normalizer


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_schema(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                chunk_hash TEXT NOT NULL,
                chunk_text TEXT NOT NULL,
                UNIQUE(source_id, chunk_hash)
            )
            """
        )


def _tokenize(text: str) -> set[str]:
    return set(get_russian_normalizer().lemmas(text))


def split_into_chunks(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    return lines


@dataclass(frozen=True)
class RagChunk:
    id: int
    source_id: str
    chunk_text: str
    score: float


class RagRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        init_schema(db_path)

    def ingest(self, *, source_id: str, text: str) -> int:
        init_schema(self.db_path)
        inserted = 0
        chunks = split_into_chunks(text)
        with _connect(self.db_path) as connection:
            for chunk in chunks:
                digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO rag_chunks (source_id, chunk_hash, chunk_text)
                    VALUES (?, ?, ?)
                    """,
                    (source_id, digest, chunk),
                )
                if cursor.rowcount > 0:
                    inserted += 1
        return inserted

    def retrieve(self, *, query: str, limit: int = 3) -> list[RagChunk]:
        init_schema(self.db_path)
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        with _connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT id, source_id, chunk_text FROM rag_chunks"
            ).fetchall()

        scored: list[RagChunk] = []
        for row in rows:
            chunk_text = str(row["chunk_text"])
            chunk_tokens = _tokenize(chunk_text)
            overlap = len(query_tokens.intersection(chunk_tokens))
            if overlap <= 0:
                continue
            score = overlap / max(len(query_tokens), 1)
            scored.append(
                RagChunk(
                    id=int(row["id"]),
                    source_id=str(row["source_id"]),
                    chunk_text=chunk_text,
                    score=score,
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]
