"""Local HTTP transport for the dashboard.

The server only listens on the loopback interface. Every API call must carry
the per-run token that is embedded in the page, and the Host header must name
the loopback address; together these stop other websites (CSRF) and DNS
rebinding attacks from talking to the API.
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import re
import socket
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from agnabzi import APP_ID, __version__
from agnabzi.api import Api, ApiError
from agnabzi.system import IS_WINDOWS, static_dir

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-NetPulse-Token"
TOKEN_PLACEHOLDER = "__NETPULSE_TOKEN__"
MAX_BODY = 64 * 1024

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")

Handler = Callable[[Api, dict[str, Any], dict[str, list[str]]], Any]
_JOB_PATH = re.compile(r"^/api/jobs/([A-Za-z0-9-]{1,40})(/cancel)?$")


class RateLimiter:
    """A small sliding-window guard so a runaway page cannot spam outbound tools."""

    def __init__(self, limit: int, window: float):
        self._limit = limit
        self._window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, bucket: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(bucket, deque())
            while hits and now - hits[0] > self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(query.get(name, [default])[0])
    except (TypeError, ValueError):
        return default


GET_ROUTES: dict[str, Handler] = {
    "/api/overview": lambda api, body, q: api.overview(_query_int(q, "since", 0), _query_int(q, "window", 300)),
    "/api/apps": lambda api, body, q: api.apps(),
    "/api/connections": lambda api, body, q: api.connections(),
    "/api/adapters": lambda api, body, q: api.adapters(),
    "/api/history": lambda api, body, q: api.history(_query_int(q, "days", 30)),
    "/api/security": lambda api, body, q: api.security(),
    "/api/settings": lambda api, body, q: api.settings(),
}

POST_ROUTES: dict[str, Handler] = {
    "/api/settings": lambda api, body, q: api.update_settings(body.get("settings") or {}),
    "/api/apps/block": lambda api, body, q: api.block_app(body.get("key", "")),
    "/api/apps/unblock": lambda api, body, q: api.unblock_app(body.get("key", "")),
    "/api/apps/reveal": lambda api, body, q: api.reveal_app(body.get("key", "")),
    "/api/tools/dns": lambda api, body, q: api.dns(body.get("name")),
    "/api/tools/dnscheck": lambda api, body, q: api.dns_check(body.get("name")),
    "/api/tools/port": lambda api, body, q: api.port(body.get("host"), body.get("port")),
    "/api/tools/ping": lambda api, body, q: api.ping(body.get("host"), body.get("count", 4)),
    "/api/tools/ipinfo": lambda api, body, q: api.ip_details(body.get("ip")),
    "/api/tools/traceroute": lambda api, body, q: api.start_traceroute(body.get("host")),
    "/api/tools/speedtest": lambda api, body, q: api.start_speedtest(),
    "/api/history/reset": lambda api, body, q: api.reset_history(),
    "/api/system/autostart": lambda api, body, q: api.set_autostart(body.get("enabled")),
    "/api/system/elevate": lambda api, body, q: api.elevate(),
    "/api/system/quit": lambda api, body, q: api.quit(),
}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind the same port.
    allow_reuse_address = not IS_WINDOWS

    def __init__(self, port: int, token: str, tool_limit: int = 30):
        self.token = token
        self.api: Api | None = None
        self._index_template: str | None = None
        self.tool_limiter = RateLimiter(tool_limit, window=10.0)
        super().__init__(("127.0.0.1", port), RequestHandler)
        self.port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def server_bind(self) -> None:
        if IS_WINDOWS:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def index_html(self) -> bytes:
        if self._index_template is None:
            with open(os.path.join(static_dir(), "index.html"), encoding="utf-8") as fh:
                self._index_template = fh.read()
        return self._index_template.replace(TOKEN_PLACEHOLDER, self.token).encode("utf-8")


class RequestHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = f"{APP_ID}/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s %s", self.address_string(), format % args)

    # plumbing --------------------------------------------------------------------

    def _security_headers(self) -> None:
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store",
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, code: str, **details: Any) -> None:
        self._json(status, {"error": code, **details})

    def _host_allowed(self) -> bool:
        return (self.headers.get("Host") or "").lower() in self.server.allowed_hosts

    def _token_valid(self, query: dict[str, list[str]]) -> bool:
        supplied = self.headers.get(TOKEN_HEADER) or query.get("t", [""])[0]
        return hmac.compare_digest(supplied.encode(), self.server.token.encode())

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError("body_too_large", status=413)
        if not length:
            return {}
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise ApiError("json_required", status=415)
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            raise ApiError("invalid_json") from None
        if not isinstance(body, dict):
            raise ApiError("invalid_json")
        return body

    # dispatch --------------------------------------------------------------------

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        if not self._host_allowed():
            self._error(HTTPStatus.FORBIDDEN, "host")
            return
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            if method in ("GET", "HEAD") and not path.startswith("/api/"):
                self._serve_page(path)
                return
            if path == "/api/ping":
                self._json(HTTPStatus.OK, {"app": APP_ID, "version": __version__})
                return
            if not self._token_valid(query):
                self._error(HTTPStatus.FORBIDDEN, "token")
                return
            self._serve_api(method, path, query)
        except ApiError as exc:
            self._error(exc.status, exc.code, **exc.details)
        except (ConnectionError, TimeoutError):
            pass
        except Exception:
            log.exception("unhandled error for %s %s", method, path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal")

    def _serve_api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        api = self.server.api
        assert api is not None
        if method == "GET" and path == "/api/icon":
            png = api.icon(query.get("key", [""])[0])
            if png is None:
                self._send(HTTPStatus.NOT_FOUND, b"", "image/png")
            else:
                self._send(HTTPStatus.OK, png, "image/png", cache="private, max-age=86400")
            return
        if method == "GET" and path == "/api/export.csv":
            body = ("﻿" + api.export_csv()).encode("utf-8")
            self._send(HTTPStatus.OK, body, "text/csv; charset=utf-8",
                       extra={"Content-Disposition": 'attachment; filename="agnabzi-usage.csv"'})
            return
        job = _JOB_PATH.match(path)
        if job:
            if method == "POST" and job.group(2):
                self._read_json()
                self._json(HTTPStatus.OK, api.cancel_job(job.group(1)))
            elif method == "GET" and not job.group(2):
                self._json(HTTPStatus.OK, api.job(job.group(1)))
            else:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method")
            return
        routes = POST_ROUTES if method == "POST" else GET_ROUTES
        handler = routes.get(path)
        if handler is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found")
            return
        if path.startswith("/api/tools/") and not self.server.tool_limiter.allow("tools"):
            self._error(HTTPStatus.TOO_MANY_REQUESTS, "rate_limited")
            return
        body = self._read_json() if method == "POST" else {}
        self._json(HTTPStatus.OK, handler(api, body, query))

    def _serve_page(self, path: str) -> None:
        if path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, self.server.index_html(), "text/html; charset=utf-8")
            return
        if not path.startswith("/static/"):
            self._error(HTTPStatus.NOT_FOUND, "not_found")
            return
        root = os.path.realpath(static_dir())
        target = os.path.realpath(os.path.join(root, path[len("/static/"):]))
        if not target.startswith(root + os.sep) or not os.path.isfile(target) or target.endswith("index.html"):
            self._error(HTTPStatus.NOT_FOUND, "not_found")
            return
        with open(target, "rb") as fh:
            body = fh.read()
        content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith(("javascript", "json", "svg+xml")):
            content_type += "; charset=utf-8"
        self._send(HTTPStatus.OK, body, content_type, cache="no-cache")
