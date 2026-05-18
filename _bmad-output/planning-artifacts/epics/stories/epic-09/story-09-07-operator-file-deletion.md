# Story 09.07 — Operator file deletion (`/file_delete`, `/files_delete_all`)

## Objective
Let the trusted operator (and the admin) undo a mistaken upload. Today every
file the operator sends via `/kb_add` or a Russian free-text intent lives forever:
the binary on the private volume, the `operator_files` row in
`semantaix_operator_files.db`, the linked `knowledge_moderation_candidates`
row in `semantaix_knowledge.db`, and the resulting `rag_chunks` rows in
`semantaix_rag.db` that ground every future customer answer through
`GroundedRagAnswerer`. When a file is uploaded by accident or contains stale
information the only workaround today is a manual SQL session.

This story closes that gap with two Telegram commands and two HTTP endpoints,
plus a single cascade-delete writer module so the four storage locations stay
in sync under one transaction. Both commands require a stateless second
message ending in the literal token `confirm` — there is no callback-query
state machine and no soft-delete path.

This is a post-signoff augmentation of Epic 09 (same precedent as Story 09.06).
The original epic-09 Out-of-Scope line **"Editing or revoking previously
uploaded knowledge"** is narrowed to **"Editing previously uploaded knowledge
(deletion is in scope via Story 09.07)"** — revocation by deletion is now in
scope; editing is still out of scope.

## Scope

### In Scope
- `services/api/app/operator_files_admin.py` (new):
  - `OperatorFilesAdminWriter` with `delete(*, short_id, viewer_username,
    viewer_role)` and `delete_all_for_user(*, username)`.
  - Opens `semantaix_operator_files.db` in **read-write** mode (separate from
    the read-only `OperatorFilesView`), with `PRAGMA busy_timeout = 5000` to
    absorb concurrent writes from `bot_gateway`. ATTACHes
    `semantaix_knowledge.db` and `semantaix_rag.db` read-write so the cascade
    runs in a single `BEGIN IMMEDIATE` transaction.
  - Cascade order inside the transaction:
    1. `SELECT short_id, knowledge_candidate_id, stored_binary_path,
       source_file_name, username` for the affected rows (scoped by viewer).
    2. `DELETE FROM rag_chunks WHERE source_id IN
       ('knowledge_candidate:<id>', …)` — the `source_id` format is the same
       one used by `_perform_operator_upload` (line ~1916) and
       `approve_knowledge_candidate` (line ~1743) in `services/api/app/main.py`.
    3. `DELETE FROM kdb.knowledge_moderation_candidates WHERE id IN (…)` for
       every non-null `knowledge_candidate_id`.
    4. `DELETE FROM operator_files WHERE short_id IN (…)`.
    5. `COMMIT`.
    6. After commit, `os.unlink` each non-null `stored_binary_path`
       best-effort; collect failed paths in the response summary; do **not**
       roll back the DB cascade on a stale-binary failure.
  - Scope rule for `delete`:
    - `viewer_role == "admin"` → no `username` filter (admin can delete any
      file, consistent with admin's read-all visibility).
    - Otherwise → require `username = viewer_username` (operator deletes own
      only). A short_id outside scope is indistinguishable from "not found"
      and returns `None` so the route can emit `404`.
  - `delete_all_for_user` always scopes by exactly one `username` — even the
    admin only wipes their own uploads (the per-file admin path remains the
    way to delete someone else's file).
  - Returns a `DeletedFileSummary` dataclass with `deleted_files`,
    `deleted_chunks`, `deleted_candidates`, `deleted_binaries`,
    `failed_binary_paths: list[str]`.

- `services/api/app/admin_files.py`:
  - `DELETE /admin/files/{short_id}` — reuses
    `AdminAuthService.require_session_or_internal`; returns the summary on
    success or `404` when no row matches the caller's scope.
  - `DELETE /admin/files?confirm=true` — same auth dependency; requires the
    `confirm=true` query parameter as defense-in-depth (the bot also enforces
    a textual `confirm` token); returns summary.
  - Same `as_user` + internal-token override path used by the GET endpoints.

- `services/api/app/main.py`:
  - Construct `OperatorFilesAdminWriter(...)` next to `operator_files_view`
    and pass it into `wire_admin_files_routes`.

- `services/bot_gateway/app/api_client.py`:
  - `async def delete_operator_file(*, short_id, requester_username,
    internal_token) -> dict | None` → `DELETE /admin/files/{short_id}?as_user=…`.
    Returns `None` on 404 (same pattern as `fetch_file_inspect`); otherwise
    parses JSON and re-raises `ApiError` on other non-2xx via
    `_raise_for_status`.
  - `async def delete_all_operator_files(*, requester_username,
    internal_token) -> dict` → `DELETE /admin/files?confirm=true&as_user=…`.

- `services/bot_gateway/app/main.py`:
  - `_FILE_DELETE_TRIGGER_RE = re.compile(r"^\s*/file_delete\b",
    re.IGNORECASE)`
  - `_FILES_DELETE_ALL_TRIGGER_RE = re.compile(r"^\s*/files_delete_all\b",
    re.IGNORECASE)`
  - New top-level dispatcher `_handle_file_delete_command(normalized)` with
    the same operator-or-admin gate already used by `_handle_file_inspect_command`.
  - Wired in the message-routing block of `webhook` between the existing
    `inspect_result` / `file_lib_result` dispatch sites so the regex order
    naturally separates `/file_delete` from `/file` (the `\b` after `/file`
    fails when the next char is `_`).
  - `/file_delete <short_id>` without trailing `confirm`: resolve filename
    via `api_client.fetch_file_inspect` (also acts as the scope check); DM in
    Russian "⚠️ Будет удалён без возможности восстановить: `<filename>`.
    Подтвердите: `/file_delete <short_id> confirm`". If `fetch_file_inspect`
    returns `None` → DM "Файл `#<short_id>` не найден." and stop.
  - `/file_delete <short_id> confirm`: call `api_client.delete_operator_file`;
    on `None` → DM "Файл `#<short_id>` не найден."; otherwise DM Russian
    summary (`Удалено: 1 файл, N чанков, M кандидатов`; if
    `failed_binary_paths` non-empty append a line).
  - `/files_delete_all` without `confirm`: count caller's files via
    `operator_file_repository.list_recent(username=…, limit=very_large)`. If
    zero → DM "У вас нет сохранённых файлов." else DM "⚠️ Будет удалено
    навсегда {N} файлов. Подтвердите: `/files_delete_all confirm`".
  - `/files_delete_all confirm`: call `api_client.delete_all_operator_files`;
    DM Russian summary.
  - Token parsing: split on whitespace; `confirm` is recognised in any case
    (`confirm`, `Confirm`, `CONFIRM`); any extra args after the token are
    ignored.

### Out of Scope
- Soft-delete / undo. Hard delete only.
- Editing previously uploaded knowledge (still out of scope for epic 09).
- Inline-button confirmation UX (would require callback_query wiring not yet
  in `bot_gateway`).
- Admin-driven bulk delete across all operators
  (`/files_delete_all` is own-files-only even for admin; the per-file
  `/file_delete <short_id>` still lets admin delete anyone's file).
- Project-scoped multi-tenant deletion semantics (Epic 10 territory).
- Audit log of deletions beyond the standard FastAPI request log.

## Implementation Notes
- **Two writers on `semantaix_operator_files.db`.** `bot_gateway` writes
  inserts and KB-status updates; the api now also writes deletes. WAL mode is
  already enabled and supports one writer at a time across processes. The
  busy_timeout=5000ms on the api-side connection absorbs rare contention —
  in practice operator deletes happen outside the upload pipeline so
  collisions are unlikely.
- **Transactional cross-DB cascade.** `BEGIN IMMEDIATE` is taken on the
  primary (operator_files) connection; ATTACH-ed databases enrol in the same
  transaction. All DB-side cascade happens atomically; binary unlink is a
  best-effort post-commit step (a stale file on disk is harmless and the next
  re-upload re-uses its slot).
- **Idempotency.** A repeated DELETE on the same `short_id` returns `404` on
  the second call because the row is already gone — no side effects.
- **`NULL` `stored_binary_path`.** Operator uploads with
  `download_status='failed'` (Telegram getFile error path from Story 09.05)
  store no binary. The cascade skips unlink for those without raising.
- **`NULL` `knowledge_candidate_id`.** A bot-side record with no candidate
  link (e.g. download succeeded but api ingest failed before
  `set_candidate_id`) cascades only `operator_files` row + binary; nothing to
  delete in knowledge or rag.

## Test Plan

### Unit — admin writer (`tests/test_operator_files_admin.py`, new)
- `test_delete_full_cascade_for_operator_own_file` — seed file + candidate +
  rag chunks; call `OperatorFilesAdminWriter.delete(short_id=…,
  viewer_username="@alice", viewer_role="operator")`; assert all three rows
  gone, binary unlinked, summary counts match.
- `test_delete_returns_none_for_other_owner_when_operator` — seed file owned
  by `@bob`; operator `@alice` delete returns `None`; bob's data untouched.
- `test_delete_for_admin_succeeds_on_other_owner` — admin deletes `@bob`'s
  file; cascade verified.
- `test_delete_unknown_short_id_returns_none` — `None`, no side effects.
- `test_delete_with_null_stored_binary_path` — `deleted_binaries == 0`,
  `failed_binary_paths == []`.
- `test_delete_with_null_candidate_link` — `deleted_candidates == 0,
  deleted_chunks == 0`; operator_files row removed.
- `test_delete_unlink_failure_recorded_in_summary` — monkeypatch `os.unlink`
  to raise; DB cascade still committed; `failed_binary_paths` contains the
  path.
- `test_delete_all_for_user_only_touches_own_rows` — seed @alice (3 files),
  @bob (2 files); delete_all_for_user(username="@alice") → returns 3 in
  summary; @bob's 2 rows + chunks + candidates intact.
- `test_delete_all_returns_zero_summary_when_no_files` — no rows,
  `deleted_files == 0`.

### API — routes (`tests/test_api_admin_files.py`, additions)
- `test_delete_single_operator_own_file` — login as `@alice`; DELETE; assert
  200 + summary; second DELETE → 404.
- `test_delete_single_operator_other_owner_returns_404` — `@alice` tries to
  DELETE `@bob`'s file → 404, bob's row still there.
- `test_delete_single_admin_can_delete_others_file` — admin deletes
  `@alice`'s confidential file → 200; cascade verified.
- `test_delete_single_unknown_short_id` — 404.
- `test_delete_single_via_internal_token_and_as_user` — `Authorization:
  Bearer <token>` + `as_user=@alice` → 200.
- `test_delete_single_internal_token_missing_as_user` — 400.
- `test_delete_all_requires_confirm_query_param` — DELETE `/admin/files`
  without `confirm=true` → 400; with `confirm=true` → 200 summary.
- `test_delete_all_operator_scope_to_self` — only own rows gone.
- `test_delete_all_admin_scope_to_self` — admin's own rows only (others
  untouched).
- `test_delete_all_internal_token_path` — succeeds with bearer + as_user +
  confirm.
- `test_delete_requires_session_when_no_internal_token` — 401.
- `test_delete_single_cascades_rag_chunks_and_candidate` — seed a
  knowledge_candidate row + ingest 2 rag chunks with
  `source_id=knowledge_candidate:<id>`; verify both gone after DELETE.

### Bot gateway — dispatch (`tests/test_bot_gateway_file_library.py`, additions)
- `test_file_delete_without_confirm_emits_warning_and_no_api_call` — seed
  one file; send `/file_delete <short_id>`; DM contains filename + the
  Russian "Подтвердите" hint; the fake `api_client.delete_operator_file`
  function is **not** called.
- `test_file_delete_unknown_short_id_emits_not_found` — fake
  `fetch_file_inspect` returns `None`; DM contains "не найден".
- `test_file_delete_with_confirm_invokes_api_and_dms_summary` — fake
  `delete_operator_file` returns `{"deleted_files":1,"deleted_chunks":3,
  "deleted_candidates":1,"deleted_binaries":1,"failed_binary_paths":[]}`; DM
  contains the counts in Russian.
- `test_file_delete_confirm_token_case_insensitive` — `/file_delete X1
  Confirm` and `CONFIRM` both trigger the api call.
- `test_file_delete_confirm_returns_404_dms_not_found` — fake
  `delete_operator_file` returns `None`; DM "не найден".
- `test_file_delete_from_non_operator_non_admin_ignored` — neither operator
  nor admin → no DM, no api call.
- `test_files_delete_all_without_confirm_zero_files` — empty repo; DM "У вас
  нет сохранённых файлов".
- `test_files_delete_all_without_confirm_with_files` — seed 3 files; DM
  contains "3 файлов" + Russian hint; no api call.
- `test_files_delete_all_with_confirm_invokes_api_and_dms_summary` — fake
  returns summary `{"deleted_files":3,…}`; DM summary contains the counts.
- `test_files_delete_all_admin_uses_own_username` — admin sends
  `/files_delete_all confirm`; api call carries admin's username as
  `requester_username`.

### Integration — coverage
- The new repo-level cascade and route-level scope checks are covered above
  with TestClient against the real FastAPI app and real ephemeral SQLite
  files. No new file under `tests/e2e/` is added — the existing
  `tests/e2e/test_e2e_epic09_*.py` continues to cover the upload happy path.

## Automated E2E verification
Story-aligned rows added to
`_bmad-output/implementation-artifacts/e2e-coverage.md`:
- Single-file delete cascade (operator/admin scopes).
- Bulk delete `/admin/files?confirm=true` scope to caller's username only.
- Bot dispatch with stateless `confirm` token.

## Manual Verification
1. `docker compose up --build -d` and ensure all five services are healthy.
2. From the primary operator, send a small txt with caption `/kb_add`
   containing "Парковка работает с 8 утра до 22 вечера". The summary DM
   should show a non-zero `chunks` count and a `#<short_id>` line.
3. From a customer account, ask "когда работает парковка?" — the bot answer
   should mention the time (proves the rag chunk grounds the answer).
4. From the operator, send `/file_delete <short_id>` — DM is the warning
   with filename + Russian confirmation hint. No deletion happens yet.
5. From the operator, send `/file_delete <short_id> confirm` — DM is the
   summary with `1 файл`, non-zero `чанков`, `1 кандидат`. Verify the
   binary disappeared from `.data/operator_uploads/` inside the container.
6. From the customer account, ask "когда работает парковка?" again — the
   answer should now escalate to HITL (no grounded chunk left).
7. Upload three more small files, then send `/files_delete_all` — DM is the
   3-files warning. Send `/files_delete_all confirm` — DM is the summary
   `3 файла`. All three short_ids no longer appear in `/files`.

## Done Criteria
- `ruff check .` passes.
- `pytest --cov --cov-config=.coveragerc --cov-report=term-missing` passes
  with 100% coverage on `platform_common/` and `services/`.
- All new tests above pass.
- All existing Epic 09 tests stay green.
- Manual verification steps 2–7 produce the expected Russian DM strings and
  the post-delete customer query escalates to HITL.
