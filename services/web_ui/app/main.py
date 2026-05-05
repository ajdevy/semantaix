from fastapi.responses import HTMLResponse

from platform_common.app_factory import create_service_app


app = create_service_app("web_ui")


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
