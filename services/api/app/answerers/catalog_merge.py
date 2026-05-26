"""Merge structured ``project_services`` rows with the LLM-built catalog digest.

The catalog answer branch (``GroundedRagAnswerer``) now reads structured rows
**first** (story 13.06 / FR-25). When both a structured set and a digest exist
this helper deduplicates digest sentences whose lemma set is a superset of any
structured row's name-lemmas, then returns the merged chunk plus a source-id
suffix used in the ``answer_traces.source_id`` field.

Dedup is **conservative** — when the lemma overlap is partial (e.g. structured
``"стрижка детская"`` vs digest ``"стрижка мужская"``) both are kept.
Over-include is safer than under-include per FR-25.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from services.api.app.calendar.project_services_repository import ProjectService
from services.api.app.services_render import (
    get_terms_data,
    render_project_services_block,
)

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class _Normalizer(Protocol):
    def lemmas(self, text: str) -> list[str]: ...


def _sentences(text: str) -> list[str]:
    """Split text into rough Russian sentences (digest paragraphs).

    Digests are LLM-built bullet lists ("- foo\n- bar"). Split first on
    newlines (each bullet is its own unit), then on sentence terminators
    so a single bullet that happens to span two sentences is still
    deduplicated cleanly.
    """
    parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for chunk in _SENTENCE_SPLIT_RE.split(stripped):
            chunk_stripped = chunk.strip()
            if chunk_stripped:
                parts.append(chunk_stripped)
    return parts


def merge_structured_with_digest(
    *,
    structured_rows: list[ProjectService],
    digest_text: str,
    normalizer: _Normalizer,
    trace_id: str | None = None,
    project_id: int | None = None,
) -> tuple[str, str]:
    """Merge structured rows with a digest into a single grounded chunk.

    Returns ``(merged_chunk_text, source_id_suffix)`` where the suffix is
    one of ``"project_services"``, ``"catalog_digest"``, ``"merged"``, or
    ``"empty"``. The caller (grounded_rag) maps ``"empty"`` to the existing
    ``_skip(reason="catalog_empty")`` fallthrough.
    """
    digest_clean = (digest_text or "").strip()
    has_structured = bool(structured_rows)
    has_digest = bool(digest_clean)

    if not has_structured and not has_digest:
        logger.info(
            "catalog_merge_dedup",
            extra={
                "trace_id": trace_id,
                "project_id": project_id,
                "structured_count": 0,
                "digest_present": False,
                "dedup_matches": 0,
                "source_id": "empty",
            },
        )
        return "", "empty"

    if not has_structured:
        logger.info(
            "catalog_merge_dedup",
            extra={
                "trace_id": trace_id,
                "project_id": project_id,
                "structured_count": 0,
                "digest_present": True,
                "dedup_matches": 0,
                "source_id": "catalog_digest",
            },
        )
        return digest_clean, "catalog_digest"

    terms = get_terms_data()
    structured_prose = render_project_services_block(
        structured_rows, terms=terms
    )

    if not has_digest:
        logger.info(
            "catalog_merge_dedup",
            extra={
                "trace_id": trace_id,
                "project_id": project_id,
                "structured_count": len(structured_rows),
                "digest_present": False,
                "dedup_matches": 0,
                "source_id": "project_services",
            },
        )
        return structured_prose, "project_services"

    # Both contribute — dedup digest sentences against structured row names.
    name_lemma_sets: list[set[str]] = []
    for row in structured_rows:
        name = (row.name or "").strip()
        if not name:
            continue
        lemmas = set(normalizer.lemmas(name))
        if lemmas:
            name_lemma_sets.append(lemmas)

    kept_sentences: list[str] = []
    dedup_matches = 0
    for sentence in _sentences(digest_clean):
        sentence_lemmas = set(normalizer.lemmas(sentence))
        covered = False
        for name_lemmas in name_lemma_sets:
            if name_lemmas and name_lemmas.issubset(sentence_lemmas):
                covered = True
                break
        if covered:
            dedup_matches += 1
        else:
            kept_sentences.append(sentence)

    deduped_digest = "\n".join(kept_sentences)
    if deduped_digest:
        merged = f"{structured_prose}\n{deduped_digest}"
    else:
        merged = structured_prose

    logger.info(
        "catalog_merge_dedup",
        extra={
            "trace_id": trace_id,
            "project_id": project_id,
            "structured_count": len(structured_rows),
            "digest_present": True,
            "dedup_matches": dedup_matches,
            "source_id": "merged",
        },
    )
    return merged, "merged"


__all__: tuple[str, ...] = ("merge_structured_with_digest",)
