"""Services natural-language operations (Epic 13, story 13.04).

State-machine + regex parser for the FR-24 Path B operator dialog:

- ``parse_service_intent`` — start-of-message-anchored, ё/е- and Cyrillic-dash-
  insensitive regex that maps a Russian operator utterance to one of four
  canonical op-types (``add``, ``edit``, ``remove``, ``OP_UNKNOWN``). Fails
  closed on ambiguity (multiple services in one utterance, non-digit duration,
  ...). NO LLM.
- ``ServicesNlOpsRepository`` — mirrors :mod:`services.api.app.admin_nl_ops`'s
  state machine on the operator + project-scoped ``services_nl_op_sessions``
  table created in story 13.01. Single-pending-per-``(project, operator)``
  invariant is enforced atomically inside ``BEGIN IMMEDIATE`` (the prior
  pending row is flipped to ``cancelled`` with reason ``replaced_by_new_pending``
  before the new row is committed; concurrent proposes from the same operator
  cannot end with two pending rows).
- ``apply_confirmed`` — helper for the api endpoint that takes a confirmed
  session and applies the side-effect via :class:`ProjectServiceRepository`
  under :func:`acquire_service_upsert_lock`. Emits the FULL-payload structured
  log ``services_nl_op_confirmed`` per the H5 decision (operator-published
  service content is not a secret; the FR-18/NFR-3 redaction rule remains
  scoped to OAuth tokens / encryption keys).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from services.api.app.calendar.project_services_repository import (
    ProjectServiceNotFound,
    ProjectServiceRepository,
)

logger = logging.getLogger(__name__)


# --- Status / op-type vocabulary ---------------------------------------------

STATUS_PENDING = "pending_confirmation"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
STATUS_CLARIFY = "clarify"

OP_SERVICE_ADD = "service_add"
OP_SERVICE_EDIT = "service_edit"
OP_SERVICE_REMOVE = "service_remove"
OP_UNKNOWN = "OP_UNKNOWN"

CANCEL_REASON_OPERATOR = "operator_cancel"
CANCEL_REASON_REPLACED = "replaced_by_new_pending"

_DEFAULT_PENDING_TTL_SECONDS = 600
_MAX_OPERATOR_TEXT_CHARS = 200

REASON_MULTIPLE_SERVICES = "multiple_services_in_one_utterance"
REASON_NON_DIGIT_DURATION = "non_digit_duration"
REASON_UNRECOGNIZED = "unrecognized_intent"


# --- Exceptions --------------------------------------------------------------


class InvalidConfirmToken(Exception):
    """Raised when a confirm call uses a wrong/missing token."""


class NlOpSessionNotOwner(Exception):
    """Raised when the presenter operator does not own the session."""


class NlOpSessionNotPending(Exception):
    """Raised when confirm/cancel targets a session that is not pending."""


class NlOpSessionExpired(Exception):
    """Raised when a confirm call hits a session past its expires_at."""


class NlOpSessionNotFound(LookupError):
    """Raised when a session_id does not match any row."""


# --- Intent dataclass --------------------------------------------------------


@dataclass(frozen=True)
class IntentMatch:
    op_type: str
    payload: dict[str, object]
    preview: str
    reason: str | None = None


# --- Regex parser ------------------------------------------------------------

# Cyrillic dash variants: hyphen-minus, en-dash, em-dash, minus sign.
_DASH_VARIANTS = "-–—−"
_DASH_RE = re.compile(f"[{re.escape(_DASH_VARIANTS)}]")


def _normalize_dashes_and_yo(text: str) -> str:
    """Collapse ё/е + dash variants so a single regex matches every form."""
    return _DASH_RE.sub("-", text.replace("ё", "е").replace("Ё", "Е"))


# Trigger keyword classifies into one of the three op-types.
_ADD_KEYWORDS = ("добавь", "добавьте", "новая", "создай")
_EDIT_KEYWORDS = ("измени", "измените")
_REMOVE_KEYWORDS = ("удали", "удалите")

_TRIGGER_RE = re.compile(
    r"^\s*(?P<verb>добавь|добавьте|новая|создай|измени|измените|удали|удалите)"
    r"\s+услуг[ауы]\b\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

# Free-form non-digit duration markers that should fail closed when present
# WITHOUT a digit-minute clause. "час", "полтора", "пол часа", etc.
_NON_DIGIT_DURATION_RE = re.compile(
    r"\b(?:полтора|пол\s*-?\s*часа|часа?|часов)\b",
    re.IGNORECASE,
)

# Names + arbitrary tail; fields are extracted from the tail by sub-regexes.
_DURATION_RE = re.compile(
    r"(?:на\s+(?P<d1>\S+)\s+минут|"
    r"(?P<d2>\S+)\s+мин(?:ут)?\b|"
    r"длительность\s+(?P<d3>\S+)\s+мин(?:ут)?\b)",
    re.IGNORECASE,
)
# Match a duration phrase to detect that the operator tried to specify one.
_DURATION_PRESENCE_RE = re.compile(
    r"(?:на\s+\S+\s+минут|\S+\s+мин(?:ут)?\b|длительность\s+\S+\s+мин(?:ут)?\b)",
    re.IGNORECASE,
)

# Cyrillic short day names -> ISO short codes used by ProjectServiceRepository.
_DAY_NAMES = {
    "пн": "mon",
    "вт": "tue",
    "ср": "wed",
    "чт": "thu",
    "пт": "fri",
    "сб": "sat",
    "вс": "sun",
    "понедельник": "mon",
    "вторник": "tue",
    "среда": "wed",
    "четверг": "thu",
    "пятница": "fri",
    "суббота": "sat",
    "воскресенье": "sun",
}
_DAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_DAYS_RANGE_RE = re.compile(
    r"(?P<from>пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)"
    r"\s*-\s*"
    r"(?P<to>пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)",
    re.IGNORECASE,
)

_HOURS_RE = re.compile(
    r"(?<![\d:])(?P<sh>\d{1,2})(?::(?P<sm>\d{2}))?\s*-\s*"
    r"(?P<eh>\d{1,2})(?::(?P<em>\d{2}))?(?!\d)",
)

_PRICE_RE = re.compile(
    r"цена(?:\s+от)?\s+(?P<price>[\w\s₽]+?)(?=\s+(?:описание|пн|вт|ср|чт|пт|сб|вс|"
    r"длительность|на\s+\d)|$|,)",
    re.IGNORECASE,
)

_DESC_RE = re.compile(r"описание\s*[:\-]?\s*(?P<desc>.+)$", re.IGNORECASE | re.DOTALL)

# Multi-service guard: detect "и педикюр", "и стрижка" style conjunctions in the
# name portion before any structured field. The conjunction is treated as a
# fail-closed signal because the parser can only model one service per utterance.
_MULTI_SERVICE_RE = re.compile(r"\s+и\s+\S+", re.IGNORECASE)


def _clip_operator_text(value: str) -> str:
    """Strip control chars + clip to the operator-content length cap."""
    cleaned = "".join(ch for ch in value if ch == "\n" or ch == "\t" or ch >= " ")
    cleaned = cleaned.strip()
    if len(cleaned) > _MAX_OPERATOR_TEXT_CHARS:
        return cleaned[:_MAX_OPERATOR_TEXT_CHARS]
    return cleaned


def _expand_days_range(start: str, end: str) -> list[str]:
    start_code = _DAY_NAMES[start.lower()]
    end_code = _DAY_NAMES[end.lower()]
    start_idx = _DAY_ORDER.index(start_code)
    end_idx = _DAY_ORDER.index(end_code)
    if end_idx < start_idx:
        return []
    return list(_DAY_ORDER[start_idx : end_idx + 1])


def _format_hours(hours: tuple[str, str]) -> str:
    return f"{hours[0]}-{hours[1]}"


def _extract_name(rest_before_fields: str) -> str:
    """The leading words before any field-marker are the service name."""
    name = rest_before_fields.strip()
    # Strip trailing punctuation that may have leaked in.
    name = name.rstrip(",;:")
    return name


def _strip_fields_from_name_section(rest: str) -> tuple[str, str]:
    """Split rest into (name_section, fields_section).

    The first match of any field-marker regex (duration, days range, price,
    description) ends the name section.
    """
    candidates: list[int] = []
    for pattern in (
        _DURATION_PRESENCE_RE,
        _DAYS_RANGE_RE,
        _PRICE_RE,
        _DESC_RE,
    ):
        m = pattern.search(rest)
        if m:
            candidates.append(m.start())
    if not candidates:
        return rest.strip(), ""
    split_at = min(candidates)
    return rest[:split_at].rstrip(), rest[split_at:]


def parse_service_intent(text: str) -> IntentMatch:
    """Map a Russian operator utterance to a structured service op.

    Start-of-message anchored, ё/е + Cyrillic-dash insensitive, fails closed
    on ambiguity (returns ``OP_UNKNOWN`` with a reason).
    """
    if not isinstance(text, str):
        return IntentMatch(
            op_type=OP_UNKNOWN,
            payload={},
            preview="не понял, отправьте текстом одну операцию по услуге.",
            reason=REASON_UNRECOGNIZED,
        )
    normalized = _normalize_dashes_and_yo(text)
    trigger = _TRIGGER_RE.match(normalized)
    if trigger is None:
        return IntentMatch(
            op_type=OP_UNKNOWN,
            payload={"raw_text": _clip_operator_text(text)},
            preview=(
                "не понял, начните с «добавь услугу <название> …», "
                "«измени услугу …» или «удали услугу …»."
            ),
            reason=REASON_UNRECOGNIZED,
        )
    verb = trigger.group("verb").lower()
    rest = trigger.group("rest")
    if verb in _REMOVE_KEYWORDS:
        op_type = OP_SERVICE_REMOVE
    elif verb in _EDIT_KEYWORDS:
        op_type = OP_SERVICE_EDIT
    else:
        op_type = OP_SERVICE_ADD

    # Fail closed on non-digit duration phrases ("на полтора часа", "на час").
    # This must run before name extraction because the name section may
    # otherwise greedily absorb the phrase.
    if op_type != OP_SERVICE_REMOVE:
        non_digit_match = _NON_DIGIT_DURATION_RE.search(rest)
        digit_match = _DURATION_PRESENCE_RE.search(rest)
        if non_digit_match is not None and digit_match is None:
            return IntentMatch(
                op_type=OP_UNKNOWN,
                payload={"raw_text": _clip_operator_text(text)},
                preview="укажите длительность числом в минутах.",
                reason=REASON_NON_DIGIT_DURATION,
            )

    name_section, fields_section = _strip_fields_from_name_section(rest)
    name = _extract_name(name_section)

    # Reject names that look like two services joined by "и" (e.g. "маникюр и педикюр").
    if op_type != OP_SERVICE_REMOVE and _MULTI_SERVICE_RE.search(name):
        return IntentMatch(
            op_type=OP_UNKNOWN,
            payload={"raw_text": _clip_operator_text(text)},
            preview="не понял, добавьте по одной услуге за раз.",
            reason=REASON_MULTIPLE_SERVICES,
        )

    if not name:
        return IntentMatch(
            op_type=OP_UNKNOWN,
            payload={"raw_text": _clip_operator_text(text)},
            preview="не понял, укажите название услуги после слова «услугу».",
            reason=REASON_UNRECOGNIZED,
        )

    name_clipped = _clip_operator_text(name)
    payload: dict[str, object] = {"name": name_clipped}

    if op_type == OP_SERVICE_REMOVE:
        preview = f"Удалить услугу «{name_clipped}»."
        return IntentMatch(op_type=op_type, payload=payload, preview=preview)

    # Duration — must be digits when present.
    duration_minutes: int | None = None
    duration_phrase = _DURATION_PRESENCE_RE.search(fields_section)
    if duration_phrase is not None:
        dm = _DURATION_RE.search(fields_section)
        raw_dur: str | None = None
        if dm is not None:
            raw_dur = dm.group("d1") or dm.group("d2") or dm.group("d3")
        if raw_dur is None or not raw_dur.isdigit():
            return IntentMatch(
                op_type=OP_UNKNOWN,
                payload={"raw_text": _clip_operator_text(text)},
                preview="укажите длительность числом в минутах.",
                reason=REASON_NON_DIGIT_DURATION,
            )
        duration_minutes = int(raw_dur)
        payload["duration_minutes"] = duration_minutes

    # Days range.
    days: list[str] = []
    days_match = _DAYS_RANGE_RE.search(fields_section)
    if days_match is not None:
        days = _expand_days_range(days_match.group("from"), days_match.group("to"))
        if days:
            payload["service_days"] = days

    # Hours.
    hours_pair: tuple[str, str] | None = None
    hours_match = _HOURS_RE.search(fields_section)
    if hours_match is not None:
        sh = int(hours_match.group("sh"))
        sm = int(hours_match.group("sm") or 0)
        eh = int(hours_match.group("eh"))
        em = int(hours_match.group("em") or 0)
        if 0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59:
            hours_pair = (f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}")
            if days:
                payload["working_hours"] = {
                    day: [[hours_pair[0], hours_pair[1]]] for day in days
                }

    # Price.
    price_text: str | None = None
    price_match = _PRICE_RE.search(fields_section)
    if price_match is not None:
        price_text = _clip_operator_text(price_match.group("price"))
        if price_text:
            payload["price_text"] = price_text

    # Description.
    description: str | None = None
    desc_match = _DESC_RE.search(fields_section)
    if desc_match is not None:
        description = _clip_operator_text(desc_match.group("desc"))
        if description:
            payload["description"] = description

    # Preview construction (deterministic, plain text).
    preview_bits: list[str] = []
    if duration_minutes is not None:
        preview_bits.append(f"{duration_minutes} мин")
    if days and hours_pair is not None:
        first = days[0]
        last = days[-1]
        # Inverse-lookup the short Cyrillic for display.
        ru_short = {v: k for k, v in _DAY_NAMES.items() if len(k) == 2}
        preview_bits.append(
            f"{ru_short.get(first, first)}-{ru_short.get(last, last)} "
            f"{_format_hours(hours_pair)}"
        )
    elif days:
        ru_short = {v: k for k, v in _DAY_NAMES.items() if len(k) == 2}
        preview_bits.append(f"{ru_short.get(days[0], days[0])}-{ru_short.get(days[-1], days[-1])}")
    if price_text:
        preview_bits.append(f"цена {price_text}")
    verb_phrase = "Создать" if op_type == OP_SERVICE_ADD else "Изменить"
    if preview_bits:
        details = ", ".join(preview_bits)
        preview = f"{verb_phrase} услугу «{name_clipped}» ({details})."
    else:
        preview = f"{verb_phrase} услугу «{name_clipped}»."

    return IntentMatch(op_type=op_type, payload=payload, preview=preview)


# --- Session dataclass + persistence ----------------------------------------


@dataclass(frozen=True)
class ServicesNlSession:
    id: int
    project_id: int
    originating_operator: str
    op_type: str
    payload: dict[str, object]
    preview: str
    status: str
    confirm_token_sha256: str | None
    created_at: str
    expires_at: str
    consumed_at: str | None
    soft_deleted_at: str | None
    # Plaintext token returned ONLY by `propose`; never persisted, never read.
    plaintext_confirm_token: str | None = field(default=None, compare=False)
    # Session ids that were flipped to ``cancelled`` because this propose
    # call replaced them under the single-pending invariant. Only populated
    # on the dataclass returned directly by ``propose`` so the bot dispatcher
    # can DM the operator a "previous request cancelled" notice before the
    # new preview. Never persisted; always empty on reload via ``get``.
    prior_cancelled_session_ids: tuple[int, ...] = field(
        default=(), compare=False
    )


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    return connection


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_to_session(row: sqlite3.Row) -> ServicesNlSession:
    payload_raw = str(row["payload_json"])
    payload = json.loads(payload_raw) if payload_raw else {}
    return ServicesNlSession(
        id=int(row["id"]),
        project_id=int(row["project_id"]),
        originating_operator=str(row["originating_operator"]),
        op_type=str(row["op_type"]),
        payload=payload,
        preview=str(row["preview"]),
        status=str(row["status"]),
        confirm_token_sha256=(
            str(row["confirm_token_sha256"])
            if row["confirm_token_sha256"] is not None
            else None
        ),
        created_at=str(row["created_at"]),
        expires_at=str(row["expires_at"]),
        consumed_at=(
            str(row["consumed_at"]) if row["consumed_at"] is not None else None
        ),
        soft_deleted_at=(
            str(row["soft_deleted_at"])
            if row["soft_deleted_at"] is not None
            else None
        ),
    )


def _log_event(
    name: str,
    *,
    session: ServicesNlSession,
    extra: dict[str, object] | None = None,
) -> None:
    """Emit a structured event with full payload visibility.

    Per the H5 decision, operator-published service content is non-secret;
    the FR-18/NFR-3 redaction rule remains scoped to OAuth tokens / encryption
    keys. We deliberately log the whole payload.
    """
    payload = dict(session.payload)
    log_extra: dict[str, object] = {
        "session_id": session.id,
        "project_id": session.project_id,
        "operator": session.originating_operator,
        "op_type": session.op_type,
        # NOTE: ``name`` collides with the reserved ``LogRecord.name`` attribute;
        # use ``service_name`` so logging.makeRecord accepts the extra dict.
        "service_name": payload.get("name"),
        "description": payload.get("description"),
        "price_text": payload.get("price_text"),
        "tags_json": payload.get("tags"),
        "duration_minutes": payload.get("duration_minutes"),
        "working_hours_json": payload.get("working_hours"),
        "service_days_json": payload.get("service_days"),
        "date_exceptions_json": payload.get("date_exceptions"),
    }
    if extra:
        log_extra.update(extra)
    logger.info(name, extra=log_extra)


class ServicesNlOpsRepository:
    def __init__(
        self,
        *,
        db_path: str,
        pending_ttl_seconds: int = _DEFAULT_PENDING_TTL_SECONDS,
    ) -> None:
        self.db_path = db_path
        self._pending_ttl = pending_ttl_seconds
        # The table itself is created by ``init_services_nl_ops_schema`` at
        # api boot; calling it here keeps repo construction stand-alone for
        # tests + makes the bootstrap doubly-idempotent.
        from services.api.app.calendar.services_nl_op_session_repository import (
            init_services_nl_ops_schema,
        )

        init_services_nl_ops_schema(db_path)

    # -- Read helpers --------------------------------------------------------

    def _get(self, session_id: int) -> ServicesNlSession:
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, project_id, originating_operator, op_type, payload_json,
                       preview, confirm_token_sha256, status, created_at, expires_at,
                       consumed_at, soft_deleted_at
                FROM services_nl_op_sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise NlOpSessionNotFound(f"session_not_found:{session_id}")
        return _row_to_session(row)

    def get(self, session_id: int) -> ServicesNlSession:
        return self._get(session_id)

    # -- Mutations -----------------------------------------------------------

    def propose(
        self,
        *,
        project_id: int,
        originating_operator: str,
        text: str,
        now: datetime | None = None,
        ttl_seconds: int | None = None,
    ) -> ServicesNlSession:
        """Parse intent + insert a session row.

        - On ``OP_UNKNOWN``: status ``clarify``; no confirm token.
        - On a parsed intent: status ``pending_confirmation``; confirm token
          generated, sha256 stored, plaintext returned on the dataclass.

        Single-pending invariant: any existing ``pending_confirmation`` row for
        the same ``(project_id, originating_operator)`` is flipped to
        ``cancelled`` (with ``replaced_by_new_pending`` in soft_deleted_at) in
        the same ``BEGIN IMMEDIATE`` transaction as the insert.
        """
        intent = parse_service_intent(text)
        now_dt = now or _utcnow()
        ttl = ttl_seconds if ttl_seconds is not None else self._pending_ttl
        expires_at = now_dt + timedelta(seconds=ttl)
        plaintext_token: str | None = None
        token_hash: str | None = None
        if intent.op_type == OP_UNKNOWN:
            status = STATUS_CLARIFY
            payload = dict(intent.payload)
            if intent.reason:
                payload["reason"] = intent.reason
        else:
            status = STATUS_PENDING
            plaintext_token = secrets.token_urlsafe(16)
            token_hash = _hash_token(plaintext_token)
            payload = dict(intent.payload)
        payload_json = json.dumps(payload, ensure_ascii=False)
        replaced_sessions: list[ServicesNlSession] = []
        with _connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if status == STATUS_PENDING:
                    prior_rows = connection.execute(
                        """
                        SELECT id, project_id, originating_operator, op_type, payload_json,
                               preview, confirm_token_sha256, status, created_at,
                               expires_at, consumed_at, soft_deleted_at
                        FROM services_nl_op_sessions
                        WHERE project_id = ? AND originating_operator = ?
                              AND status = ?
                        """,
                        (project_id, originating_operator, STATUS_PENDING),
                    ).fetchall()
                    for row in prior_rows:
                        connection.execute(
                            """
                            UPDATE services_nl_op_sessions
                            SET status = ?, confirm_token_sha256 = NULL,
                                soft_deleted_at = ?
                            WHERE id = ?
                            """,
                            (STATUS_CANCELLED, CANCEL_REASON_REPLACED, int(row["id"])),
                        )
                        replaced_sessions.append(_row_to_session(row))
                cursor = connection.execute(
                    """
                    INSERT INTO services_nl_op_sessions (
                        project_id, originating_operator, op_type, payload_json,
                        preview, confirm_token_sha256, status, created_at,
                        expires_at, consumed_at, soft_deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                    """,
                    (
                        project_id,
                        originating_operator,
                        intent.op_type,
                        payload_json,
                        intent.preview,
                        token_hash,
                        status,
                        _iso(now_dt),
                        _iso(expires_at),
                    ),
                )
                connection.execute("COMMIT")
                row_id = int(cursor.lastrowid)
            except Exception:
                connection.execute("ROLLBACK")
                raise
        session = self._get(row_id)
        for replaced in replaced_sessions:
            cancelled_session = self._get(replaced.id)
            _log_event(
                "services_nl_op_cancelled",
                session=cancelled_session,
                extra={"cancellation_reason": CANCEL_REASON_REPLACED},
            )
        prior_ids = tuple(r.id for r in replaced_sessions)
        if plaintext_token is not None or prior_ids:
            session = ServicesNlSession(
                id=session.id,
                project_id=session.project_id,
                originating_operator=session.originating_operator,
                op_type=session.op_type,
                payload=session.payload,
                preview=session.preview,
                status=session.status,
                confirm_token_sha256=session.confirm_token_sha256,
                created_at=session.created_at,
                expires_at=session.expires_at,
                consumed_at=session.consumed_at,
                soft_deleted_at=session.soft_deleted_at,
                plaintext_confirm_token=plaintext_token,
                prior_cancelled_session_ids=prior_ids,
            )
        return session

    def consume(
        self,
        *,
        session_id: int,
        presenter_operator: str,
        confirm_token: str,
        now: datetime | None = None,
    ) -> ServicesNlSession:
        """Atomic single-use confirm; verifies ownership + token + TTL.

        On TTL miss flips status to ``expired`` and raises
        :class:`NlOpSessionExpired` (the row also gets a
        ``services_nl_op_expired`` audit event).
        """
        now_dt = now or _utcnow()
        session = self._get(session_id)
        if session.status != STATUS_PENDING:
            raise NlOpSessionNotPending(session.status)
        expires_at = datetime.fromisoformat(session.expires_at)
        if now_dt >= expires_at:
            with _connect(self.db_path) as connection:
                connection.execute(
                    """
                    UPDATE services_nl_op_sessions
                    SET status = ?, confirm_token_sha256 = NULL
                    WHERE id = ?
                    """,
                    (STATUS_EXPIRED, session_id),
                )
            expired = self._get(session_id)
            _log_event("services_nl_op_expired", session=expired)
            raise NlOpSessionExpired(session_id)
        if session.originating_operator != presenter_operator:
            raise NlOpSessionNotOwner(presenter_operator)
        if session.confirm_token_sha256 is None or not hmac.compare_digest(
            _hash_token(confirm_token), session.confirm_token_sha256
        ):
            raise InvalidConfirmToken()
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE services_nl_op_sessions
                SET status = ?, consumed_at = ?, confirm_token_sha256 = NULL
                WHERE id = ?
                """,
                (STATUS_CONFIRMED, _iso(now_dt), session_id),
            )
        return self._get(session_id)

    def cancel(
        self,
        *,
        session_id: int,
        presenter_operator: str,
        now: datetime | None = None,
    ) -> ServicesNlSession:
        """Cancel a pending session; ownership-checked, idempotent failure."""
        session = self._get(session_id)
        if session.status not in {STATUS_PENDING, STATUS_CLARIFY}:
            raise NlOpSessionNotPending(session.status)
        if session.originating_operator != presenter_operator:
            raise NlOpSessionNotOwner(presenter_operator)
        with _connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE services_nl_op_sessions
                SET status = ?, confirm_token_sha256 = NULL, soft_deleted_at = ?
                WHERE id = ?
                """,
                (STATUS_CANCELLED, CANCEL_REASON_OPERATOR, session_id),
            )
        cancelled = self._get(session_id)
        _log_event(
            "services_nl_op_cancelled",
            session=cancelled,
            extra={"cancellation_reason": CANCEL_REASON_OPERATOR},
        )
        return cancelled

    def latest_pending(
        self,
        *,
        project_id: int,
        operator: str,
        now: datetime | None = None,
    ) -> ServicesNlSession | None:
        """Most-recent pending session for the pair, lazy-expiring if past TTL."""
        now_dt = now or _utcnow()
        with _connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, project_id, originating_operator, op_type, payload_json,
                       preview, confirm_token_sha256, status, created_at,
                       expires_at, consumed_at, soft_deleted_at
                FROM services_nl_op_sessions
                WHERE project_id = ? AND originating_operator = ?
                      AND status = ?
                ORDER BY id DESC LIMIT 1
                """,
                (project_id, operator, STATUS_PENDING),
            ).fetchone()
        if row is None:
            return None
        session = _row_to_session(row)
        expires_at = datetime.fromisoformat(session.expires_at)
        if now_dt >= expires_at:
            with _connect(self.db_path) as connection:
                connection.execute(
                    """
                    UPDATE services_nl_op_sessions
                    SET status = ?, confirm_token_sha256 = NULL
                    WHERE id = ?
                    """,
                    (STATUS_EXPIRED, session.id),
                )
            expired = self._get(session.id)
            _log_event("services_nl_op_expired", session=expired)
            return None
        return session


# --- Confirm-path side-effect dispatcher ------------------------------------


async def apply_confirmed(
    *,
    session: ServicesNlSession,
    repo: ProjectServiceRepository,
    lock_factory,
) -> ProjectServiceRepository:
    """Apply the confirmed NL op's side-effect under the per-name lock.

    ``lock_factory`` is ``acquire_service_upsert_lock`` (or a test double) and
    returns an awaitable ``asyncio.Lock``. The actual sqlite write goes
    through ``asyncio.to_thread``.

    On success emits the FULL-payload ``services_nl_op_confirmed`` log line.
    """
    payload = session.payload
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("session_payload_missing_name")
    lock = await lock_factory(project_id=session.project_id, name=name)
    async with lock:
        if session.op_type == OP_SERVICE_REMOVE:
            existing = await asyncio.to_thread(
                repo.get_by_name, project_id=session.project_id, name=name
            )
            if existing is None:
                raise ProjectServiceNotFound(
                    f"project_service_not_found:{session.project_id}:{name}"
                )
            await asyncio.to_thread(
                repo.delete,
                project_id=session.project_id,
                service_id=existing.id,
            )
            applied_id = existing.id
        else:
            service = await asyncio.to_thread(
                repo.upsert,
                project_id=session.project_id,
                name=name,
                description=payload.get("description"),
                price_text=payload.get("price_text"),
                tags=payload.get("tags"),
                duration_minutes=payload.get("duration_minutes"),
                working_hours=payload.get("working_hours"),
                service_days=payload.get("service_days"),
                date_exceptions=payload.get("date_exceptions"),
            )
            applied_id = service.id
    _log_event(
        "services_nl_op_confirmed",
        session=session,
        extra={"applied_service_id": applied_id},
    )
    return applied_id
