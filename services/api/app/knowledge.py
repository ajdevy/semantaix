from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


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
            CREATE TABLE IF NOT EXISTS knowledge_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                candidate_hash TEXT NOT NULL,
                candidate_text TEXT NOT NULL,
                UNIQUE(conversation_id, source_message_id, candidate_hash)
            )
            """
        )


NOISE_PATTERNS = [
    re.compile(r"^(hi|hello|hey|thanks|thank you|ok|okay|cool|great)[!. ]*$", re.IGNORECASE),
    re.compile(r"^\W*$"),
]


def is_noise_text(text: str) -> bool:
    normalized = text.strip()
    if len(normalized) < 20:
        return True
    return any(pattern.match(normalized) for pattern in NOISE_PATTERNS)


def extract_candidate_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [line for line in lines if not is_noise_text(line)]


@dataclass(frozen=True)
class KnowledgeCandidate:
    id: int
    conversation_id: int
    source_message_id: int
    candidate_text: str


@dataclass(frozen=True)
class ExtractedCandidate:
    id: int
    candidate_text: str


@dataclass(frozen=True)
class ExtractFromTranscriptsResult:
    inserted: int
    new_candidates: list[ExtractedCandidate]


class KnowledgeCandidateRepository:
    def __init__(self, db_path: str, transcript_db_path: str) -> None:
        self.db_path = db_path
        self.transcript_db_path = transcript_db_path
        init_schema(db_path)

    def extract_from_transcripts(
        self, conversation_id: int | None = None
    ) -> ExtractFromTranscriptsResult:
        init_schema(self.db_path)
        with _connect(self.transcript_db_path) as source_connection:
            if conversation_id is None:
                rows = source_connection.execute(
                    """
                    SELECT conversation_id, source_message_id, text
                    FROM messages
                    ORDER BY source_message_id ASC
                    """
                ).fetchall()
            else:
                rows = source_connection.execute(
                    """
                    SELECT conversation_id, source_message_id, text
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY source_message_id ASC
                    """,
                    (conversation_id,),
                ).fetchall()

        inserted = 0
        new_candidates: list[ExtractedCandidate] = []
        with _connect(self.db_path) as target_connection:
            for row in rows:
                message_text = str(row["text"])
                for line in extract_candidate_lines(message_text):
                    digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
                    cursor = target_connection.execute(
                        """
                        INSERT OR IGNORE INTO knowledge_candidates (
                            conversation_id, source_message_id, candidate_hash, candidate_text
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            int(row["conversation_id"]),
                            int(row["source_message_id"]),
                            digest,
                            line,
                        ),
                    )
                    if cursor.rowcount > 0:
                        inserted += 1
                        row_id = int(cursor.lastrowid)
                        new_candidates.append(
                            ExtractedCandidate(id=row_id, candidate_text=line)
                        )
        return ExtractFromTranscriptsResult(
            inserted=inserted,
            new_candidates=new_candidates,
        )

    def list_candidates(self, conversation_id: int | None = None) -> list[KnowledgeCandidate]:
        init_schema(self.db_path)
        with _connect(self.db_path) as connection:
            if conversation_id is None:
                rows = connection.execute(
                    """
                    SELECT id, conversation_id, source_message_id, candidate_text
                    FROM knowledge_candidates
                    ORDER BY id ASC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, conversation_id, source_message_id, candidate_text
                    FROM knowledge_candidates
                    WHERE conversation_id = ?
                    ORDER BY id ASC
                    """,
                    (conversation_id,),
                ).fetchall()
        return [
            KnowledgeCandidate(
                id=int(row["id"]),
                conversation_id=int(row["conversation_id"]),
                source_message_id=int(row["source_message_id"]),
                candidate_text=str(row["candidate_text"]),
            )
            for row in rows
        ]
