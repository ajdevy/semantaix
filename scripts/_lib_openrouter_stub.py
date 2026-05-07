"""Tiny in-memory OpenRouter stub used by signoff scripts.

Returns a canned chat completion so demos can exercise `/suggest` end-to-end
without hitting the real provider. The response body is taken from the
``OPENROUTER_STUB_RESPONSE`` environment variable (default: "Use the reset
link via the email."), which lets a demo steer guardrails toward valid or
invalid outcomes.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length", "0"))
        if length > 0:
            self.rfile.read(length)
        response_text = os.environ.get(
            "OPENROUTER_STUB_RESPONSE", "Use the reset link via the email."
        )
        body = json.dumps(
            {"choices": [{"message": {"content": response_text}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002,D401
        return


def main() -> None:
    port = int(os.environ.get("OPENROUTER_STUB_PORT", "18500"))
    server = HTTPServer(("127.0.0.1", port), _StubHandler)
    sys.stdout.write(f"openrouter stub listening on 127.0.0.1:{port}\n")
    sys.stdout.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
