#!/usr/bin/env python3
"""Serve the dashboard as a clearly labelled TEST PREVIEW with canned review responses.

For looking at loading, failure and retry states without contacting a provider.
Every HTML page gets a fixed banner and a title prefix, every response carries
``X-Test-Preview: 1``, and every coach/decision answer and the coach status
say they are fixtures, so these pages cannot be mistaken for the real dashboard.

    .venv/bin/python tests/fixture_server.py --port 5059 [--delay 4]

It binds 127.0.0.1 only and refuses the real dashboard ports (5050, 5051).
Other POST requests (URL imports and so on) are refused with a labelled 403.
Stop it with Ctrl-C.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BANNER_TEXT = "TEST PREVIEW — NO LIVE PROVIDER"
REAL_PORTS = {5050, 5051}
BANNER = (
    '<div role="alert" data-test-preview="1" style="position:sticky;top:0;z-index:99999;padding:8px 12px;'
    'background:#b00020;color:#fff;font:700 15px/1.3 system-ui,sans-serif;text-align:center;letter-spacing:.03em">'
    f"{BANNER_TEXT} · fixture server, not the real dashboard · reviews here are canned errors</div>"
)


def create_fixture_app(data_dir: Path, delay_s: float = 4.0):
    from flask import jsonify, request

    from app import create_app

    app = create_app(data_dir, backend="files")
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.before_request
    def fixture_responses():
        if request.path == "/api/coach/status":
            return jsonify(configured=True, timeout_s=90, provider="fixture", model="none", test_preview=True)
        if request.method == "POST":
            if request.path.endswith(("/coach", "/decision-review")):
                time.sleep(delay_s)
                return jsonify(status="error", kind="fixture", retryable=True, test_preview=True,
                               error=f"{BANNER_TEXT}: fixture review only, no provider contacted.")
            return jsonify(status="error", test_preview=True, message=f"{BANNER_TEXT}: this action is disabled."), 403
        return None

    @app.after_request
    def label(response):
        response.headers["X-Test-Preview"] = "1"
        if response.mimetype == "text/html" and not response.direct_passthrough:
            html = response.get_data(as_text=True)
            html = html.replace("<title>", f"<title>{BANNER_TEXT} · ", 1)
            body = html.find("<body")
            if body >= 0:
                close = html.find(">", body) + 1
                html = html[:close] + BANNER + html[close:]
            else:
                html = BANNER + html
            response.set_data(html)
        return response

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=5059)
    parser.add_argument("--delay", type=float, default=4.0, help="Seconds before a canned review error.")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args(argv)
    if args.port in REAL_PORTS:
        raise SystemExit(f"port {args.port} belongs to the real dashboard; choose another port for a test preview")
    create_fixture_app(args.data_dir, args.delay).run(host="127.0.0.1", port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
