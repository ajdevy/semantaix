from html import escape as _esc
from typing import Annotated

import httpx
from fastapi import File, Form, UploadFile
from fastapi.responses import HTMLResponse

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.answer_trace import AnswerTraceRepository
from services.api.app.backups import BackupRepository
from services.api.app.hitl import HitlTicketRepository
from services.web_ui.app.admin import router as admin_router

app = create_service_app("web_ui")
app.include_router(admin_router)
_settings = get_settings()
backup_repository = BackupRepository(
    db_path=_settings.backup_db_path,
    archive_dir=_settings.backup_archive_dir,
    source_paths=_settings.backup_source_path_list(),
)
answer_trace_repository = AnswerTraceRepository(
    db_path=_settings.answer_trace_db_path,
    snippet_max_chars=_settings.answer_trace_snippet_max_chars,
)
hitl_ticket_repository = HitlTicketRepository(_settings.hitl_ticket_db_path)


def _default_operator_username() -> str:
    return (
        hitl_ticket_repository.get_runtime_config("hitl_primary_operator_username")
        or _settings.hitl_primary_operator_username
    )


async def _forward_upload_to_api(
    *,
    operator_username: str,
    is_confidential: bool,
    inline_text: str | None,
    upload_filename: str | None,
    upload_bytes: bytes | None,
    upload_content_type: str | None,
) -> tuple[int, dict]:
    url = f"{_settings.api_internal_base_url.rstrip('/')}/knowledge/operator_upload_multipart"
    data: dict[str, str] = {
        "operator_username": operator_username,
        "is_confidential": "true" if is_confidential else "false",
    }
    if inline_text is not None:
        data["inline_text"] = inline_text
    files = None
    if upload_bytes is not None:
        files = {
            "upload": (
                upload_filename or "upload.bin",
                upload_bytes,
                upload_content_type or "application/octet-stream",
            )
        }
    async with httpx.AsyncClient(
        timeout=_settings.operator_upload_api_timeout_seconds
    ) as client:
        response = await client.post(url, data=data, files=files)
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text or "api_returned_non_json"}
    return response.status_code, body


@app.get("/", response_class=HTMLResponse)
def admin_shell() -> str:
    return """
    <!doctype html>
    <html>
      <head><title>Semantaix Admin</title></head>
      <body>
        <h1>Semantaix Admin</h1>
        <p>Bootstrap admin shell is running.</p>
        <ul>
          <li><a href='/admin/login'>Admin panel (login required)</a></li>
          <li><a href='/knowledge-upload'>Upload to knowledge base</a></li>
          <li><a href='/answer-traces'>Answer traces</a></li>
          <li><a href='/backups'>Backups</a></li>
          <li><a href='/alerts'>Alerts</a></li>
        </ul>
      </body>
    </html>
    """


@app.get("/knowledge-upload", response_class=HTMLResponse)
def knowledge_upload_form() -> str:
    operator = _esc(_default_operator_username())
    return f"""
    <!doctype html>
    <html>
      <head><title>Upload to knowledge base</title></head>
      <body>
        <h1>Upload to knowledge base</h1>
        <p>Upload a file (PDF, DOCX, PPTX, TXT, image, audio, video) <em>or</em>
           paste raw text. The submission is auto-approved and indexed into RAG
           immediately.</p>
        <form action='/knowledge-upload' method='post'
              enctype='multipart/form-data'>
          <p>
            <label>Operator username
              <input type='text' name='operator_username'
                     value='{operator}' required />
            </label>
          </p>
          <p>
            <label>File
              <input type='file' name='upload' />
            </label>
          </p>
          <p>
            <label>Or paste text
              <br />
              <textarea name='inline_text' rows='8' cols='60'></textarea>
            </label>
          </p>
          <p>
            <label>
              <input type='checkbox' name='is_confidential' value='true' />
              Confidential (mark chunks as confidential in RAG)
            </label>
          </p>
          <p><button type='submit'>Upload</button></p>
        </form>
      </body>
    </html>
    """


def _render_upload_result(status: int, body: dict) -> str:
    if status == 200:
        candidate_id = _esc(str(body.get("candidate_id", "—")))
        source_id = _esc(str(body.get("source_id", "—")))
        inserted = _esc(str(body.get("inserted_chunks", "—")))
        chars = _esc(str(body.get("extracted_chars", "—")))
        deduplicated = "yes" if body.get("deduplicated") else "no"
        confidential = "yes" if body.get("is_confidential") else "no"
        return f"""
        <!doctype html>
        <html>
          <head><title>Upload complete</title></head>
          <body>
            <h1>Upload complete</h1>
            <dl>
              <dt>Candidate id</dt><dd>{candidate_id}</dd>
              <dt>RAG source id</dt><dd>{source_id}</dd>
              <dt>Inserted chunks</dt><dd>{inserted}</dd>
              <dt>Extracted characters</dt><dd>{chars}</dd>
              <dt>Deduplicated</dt><dd>{deduplicated}</dd>
              <dt>Confidential</dt><dd>{confidential}</dd>
            </dl>
            <p><a href='/knowledge-upload'>Upload another</a></p>
          </body>
        </html>
        """
    detail = _esc(str(body.get("detail", "unknown_error")))
    return f"""
    <!doctype html>
    <html>
      <head><title>Upload failed</title></head>
      <body>
        <h1>Upload failed</h1>
        <p>The API rejected the upload with status <code>{status}</code>:
           <code>{detail}</code>.</p>
        <p><a href='/knowledge-upload'>Back to form</a></p>
      </body>
    </html>
    """


@app.post("/knowledge-upload", response_class=HTMLResponse)
async def knowledge_upload_submit(
    operator_username: Annotated[str, Form()],
    is_confidential: Annotated[bool, Form()] = False,
    inline_text: Annotated[str | None, Form()] = None,
    upload: Annotated[UploadFile | None, File()] = None,
) -> str:
    upload_filename: str | None = None
    upload_bytes: bytes | None = None
    upload_content_type: str | None = None
    if upload is not None and (upload.filename or "").strip() != "":
        upload_filename = upload.filename
        upload_bytes = await upload.read()
        upload_content_type = upload.content_type
    forwarded_inline = inline_text if inline_text and inline_text.strip() else None
    status, body = await _forward_upload_to_api(
        operator_username=operator_username,
        is_confidential=is_confidential,
        inline_text=forwarded_inline,
        upload_filename=upload_filename,
        upload_bytes=upload_bytes,
        upload_content_type=upload_content_type,
    )
    return _render_upload_result(status, body)


@app.get("/alerts", response_class=HTMLResponse)
def alerts_shell() -> str:
    return """
    <!doctype html>
    <html>
      <head><title>Semantaix Alerts</title></head>
      <body>
        <h1>Semantaix Alerts</h1>
        <p>Use API endpoints for read/unread + ack/resolve timeline in Epic 02.</p>
      </body>
    </html>
    """


def _render_trace_list_row(trace) -> str:
    grounded = "yes" if trace.grounded else "no"
    return (
        f"<tr><td><a href='/answer-traces/{_esc(trace.trace_id)}'>{_esc(trace.trace_id)}</a></td>"
        f"<td>{_esc(trace.created_at)}</td>"
        f"<td>{_esc(trace.response_mode)}</td>"
        f"<td>{_esc(trace.guardrail_outcome)}</td>"
        f"<td>{grounded}</td></tr>"
    )


@app.get("/answer-traces", response_class=HTMLResponse)
def answer_traces_list(limit: int = 50) -> str:
    traces = answer_trace_repository.list_traces(limit=limit)
    if not traces:
        body = "<p>No answer traces persisted yet.</p>"
    else:
        rows = "".join(_render_trace_list_row(trace) for trace in traces)
        body = (
            "<table border='1' cellpadding='6'>"
            "<thead><tr><th>Trace</th><th>Created</th><th>Mode</th>"
            "<th>Guardrail</th><th>Grounded</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    return f"""
    <!doctype html>
    <html>
      <head><title>Semantaix Answer Traces</title></head>
      <body>
        <h1>Answer Traces</h1>
        <p>Read-only transparency view (Epic 08 Story 02).</p>
        {body}
      </body>
    </html>
    """


def _render_sources_section(retrieval: list[dict[str, object]]) -> str:
    if not retrieval:
        return "<p><em>No retrieval hit.</em></p>"
    rows = "".join(
        "<tr>"
        f"<td>{_esc(str(item.get('chunk_id', '')))}</td>"
        f"<td>{_esc(str(item.get('source_ref', '')))}</td>"
        f"<td>{float(item.get('score', 0.0)):.3f}</td>"
        f"<td>{_esc(str(item.get('text_snippet', '')))}</td>"
        "</tr>"
        for item in retrieval
    )
    return (
        "<table border='1' cellpadding='6'>"
        "<thead><tr><th>Chunk</th><th>Source</th><th>Score</th><th>Snippet</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


@app.get("/answer-traces/{trace_id}", response_class=HTMLResponse)
def answer_trace_detail(trace_id: str) -> str:
    try:
        trace = answer_trace_repository.get_by_trace_id(trace_id)
    except LookupError:
        return f"""
        <!doctype html>
        <html>
          <head><title>Answer trace not found</title></head>
          <body>
            <h1>Answer trace not found</h1>
            <p>No record exists for trace id <code>{_esc(trace_id)}</code>. The
               suggestion may have failed before persistence — check the
               <code>answer_trace_persistence_failures</code> incident feed.</p>
            <p><a href='/answer-traces'>Back to list</a></p>
          </body>
        </html>
        """
    reasons = ", ".join(_esc(reason) for reason in trace.guardrail_reasons) or "—"
    limitations = ", ".join(_esc(item) for item in trace.limitations) or "—"
    confidence = f"{trace.confidence:.3f}" if trace.confidence is not None else "—"
    latency = f"{trace.latency_ms} ms" if trace.latency_ms is not None else "—"
    return f"""
    <!doctype html>
    <html>
      <head><title>Why this answer — {_esc(trace.trace_id)}</title></head>
      <body>
        <h1>Why this answer</h1>
        <p><strong>Trace</strong> {_esc(trace.trace_id)} —
           created {_esc(trace.created_at)}</p>
        <p><strong>Request</strong>: {_esc(trace.request_text)}</p>

        <h2>Sources</h2>
        {_render_sources_section(trace.retrieval)}

        <h2>Policy / guardrails</h2>
        <ul>
          <li><strong>Outcome</strong>: {_esc(trace.guardrail_outcome)}</li>
          <li><strong>Reasons</strong>: {reasons}</li>
          <li><strong>Response mode</strong>: {_esc(trace.response_mode)}</li>
          <li><strong>Guardrails applied</strong>: {trace.guardrails_applied}</li>
        </ul>

        <h2>Model routing</h2>
        <ul>
          <li><strong>Model</strong>: {_esc(trace.model_id or '—')}</li>
          <li><strong>Provider</strong>: {_esc(trace.model_provider or '—')}</li>
          <li><strong>Latency</strong>: {latency}</li>
        </ul>

        <h2>Confidence / limitations</h2>
        <ul>
          <li><strong>Grounded</strong>: {trace.grounded}</li>
          <li><strong>No retrieval hit</strong>: {trace.no_retrieval_hit}</li>
          <li><strong>Confidence</strong>: {confidence}</li>
          <li><strong>Limitations</strong>: {limitations}</li>
        </ul>
        <p><a href='/answer-traces'>Back to list</a></p>
      </body>
    </html>
    """


@app.get("/backups", response_class=HTMLResponse)
def backups_shell() -> str:
    latest = backup_repository.latest_successful()
    if latest is None:
        last_block = "<p>No successful backup recorded yet.</p>"
    else:
        last_block = (
            "<dl>"
            f"<dt>Backup id</dt><dd>{latest.id}</dd>"
            f"<dt>Completed at</dt><dd>{latest.completed_at}</dd>"
            f"<dt>Archive path</dt><dd>{latest.archive_path}</dd>"
            f"<dt>Size (bytes)</dt><dd>{latest.size_bytes}</dd>"
            "</dl>"
        )
    return f"""
    <!doctype html>
    <html>
      <head><title>Semantaix Backups</title></head>
      <body>
        <h1>Semantaix Backups</h1>
        <h2>Last successful backup</h2>
        {last_block}
        <h2>Restore</h2>
        <p>POST <code>/api/backups/&lt;id&gt;/restore</code> with
           <code>confirm_token=restore-&lt;id&gt;</code> and a writable
           <code>target_root</code>. Restores are blocked unless the token matches.</p>
      </body>
    </html>
    """
