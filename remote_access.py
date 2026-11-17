#!/usr/bin/env python3
"""Temporary remote access: one login for the whole site and limits on paid AI calls.

Used only when the dashboard is started with ``--remote-auth``. The normal local
launch is unchanged.

Create the login once (written under data/remote_access/, which is gitignored):

    .venv/bin/python remote_access.py init

``auth.json`` holds the user name, a salt and a SHA-256 of the password; the
server reads only that file. ``credentials.txt`` (mode 600) holds the plain user
name and password for you to pass on; the server never reads it. The password is
random (128 bits), so a fast hash is enough. Run ``init`` again to replace the
login; the old password stops working at the next server start.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sys
import threading
import time
from pathlib import Path

from flask import Flask, Response, redirect, request

DEFAULT_DIR = Path(__file__).resolve().parent / "data" / "remote_access"
REALM = "Apex WinLab"


def _digest(salt: str, password: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def create_login(folder: Path = DEFAULT_DIR, user: str = "tester") -> Path:
    """Write a new random login; returns the path of the plain-text copy."""
    folder.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(16)
    salt = secrets.token_hex(16)
    auth = folder / "auth.json"
    auth.write_text(json.dumps({"user": user, "salt": salt, "sha256": _digest(salt, password)}), encoding="utf-8")
    auth.chmod(0o600)
    plain = folder / "credentials.txt"
    plain.write_text(f"user: {user}\npassword: {password}\n", encoding="utf-8")
    plain.chmod(0o600)
    return plain


def load_login(path: Path) -> dict:
    login = json.loads(path.read_text(encoding="utf-8"))
    if not all(isinstance(login.get(key), str) and login[key] for key in ("user", "salt", "sha256")):
        raise SystemExit(f"{path} is not a login written by 'remote_access.py init'")
    return login


def _visitor_scheme() -> str:
    """The scheme the visitor used, as reported by the tunnel (CF-Visitor, else X-Forwarded-Proto)."""
    try:
        scheme = json.loads(request.headers.get("CF-Visitor", "")).get("scheme", "")
    except (ValueError, AttributeError):
        scheme = ""
    return (scheme or request.headers.get("X-Forwarded-Proto", "")).lower()


def require_login(app: Flask, login: dict) -> None:
    """Every request, including static files and API routes, needs the login."""
    failures = {"count": 0}

    @app.before_request
    def check_login():
        # Behind the HTTPS tunnel, a plain-HTTP request is sent to HTTPS before any
        # login prompt, so the password is never sent unencrypted.
        if _visitor_scheme() == "http":
            return redirect(request.url.replace("http://", "https://", 1), 301)
        auth = request.authorization
        if auth is not None and auth.type == "basic":
            user_ok = hmac.compare_digest((auth.username or "").encode(), login["user"].encode())
            pass_ok = hmac.compare_digest(_digest(login["salt"], auth.password or ""), login["sha256"])
            if user_ok and pass_ok:
                return None
            failures["count"] += 1
            # Count only; the attempted values are never logged.
            print(f"remote access: rejected login attempt #{failures['count']}", file=sys.stderr, flush=True)
        return Response("Login required.", 401, {"WWW-Authenticate": f'Basic realm="{REALM}", charset="UTF-8"'})

    @app.after_request
    def harden(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if _visitor_scheme() == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=86400")
        return response


class CallGate:
    """Limits paid provider calls: at most ``max_concurrent`` at once and ``daily_limit`` per UTC day.

    Only calls that reach the provider pass through the gate; cached reviews do
    not. A call counts when it starts, whether or not it succeeds. The day's
    count is kept in ``usage_path`` so a restart does not reset it.
    """

    def __init__(self, usage_path: Path, max_concurrent: int = 1, daily_limit: int = 20, clock=time.time):
        self.usage_path = usage_path
        self.max_concurrent = max_concurrent
        self.daily_limit = daily_limit
        self.clock = clock
        self._running = 0
        self._lock = threading.Lock()

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self.clock()))

    def _read(self) -> dict:
        try:
            usage = json.loads(self.usage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            usage = {}
        if usage.get("date") != self._today() or not isinstance(usage.get("calls"), int):
            usage = {"date": self._today(), "calls": 0}
        return usage

    def used_today(self) -> int:
        with self._lock:
            return self._read()["calls"]

    def acquire(self) -> dict | None:
        """None when the call may go ahead (call ``release`` afterwards), else an error for the page."""
        with self._lock:
            if self._running >= self.max_concurrent:
                return {"kind": "busy", "error": "Another AI review is running. Try again in a moment.", "retryable": True}
            usage = self._read()
            if usage["calls"] >= self.daily_limit:
                return {"kind": "limit", "retryable": False,
                        "error": f"The daily limit of {self.daily_limit} new AI reviews for remote access is used up. "
                                 "Reviews already generated still open."}
            usage["calls"] += 1
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)
            self.usage_path.write_text(json.dumps(usage), encoding="utf-8")
            self._running += 1
            return None

    def release(self) -> None:
        with self._lock:
            self._running = max(0, self._running - 1)


def main(argv: list[str]) -> int:
    if argv[:1] != ["init"]:
        print("usage: remote_access.py init", file=sys.stderr)
        return 2
    plain = create_login()
    # The password itself is not printed.
    print(f"login written; the user name and password are in {plain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
