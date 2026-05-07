from fastapi.responses import HTMLResponse

from platform_common.app_factory import create_service_app
from platform_common.settings import get_settings
from services.api.app.backups import BackupRepository

app = create_service_app("web_ui")
_settings = get_settings()
backup_repository = BackupRepository(
    db_path=_settings.backup_db_path,
    archive_dir=_settings.backup_archive_dir,
    source_paths=_settings.backup_source_path_list(),
)


@app.get("/", response_class=HTMLResponse)
def admin_shell() -> str:
    return """
    <!doctype html>
    <html>
      <head><title>Semantaix Admin</title></head>
      <body>
        <h1>Semantaix Admin</h1>
        <p>Bootstrap admin shell is running.</p>
      </body>
    </html>
    """


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
