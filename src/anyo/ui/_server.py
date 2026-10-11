"""Browser authentication and HTTP views over the execution-owned contracts."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from importlib.resources import files
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from anyo.execution._contracts import CapacityError, QueueMismatch, QueueUnavailable
from anyo.execution._operations import OperationsBackend
from anyo.execution._worker import DurableDispatcher

_COOKIE = "anyo_session"
_SESSION_SECONDS = 8 * 3600
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; font-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
}


@dataclass(frozen=True)
class UICredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> UICredentials:
        username = os.environ.get("ANYO_UI_USERNAME", "")
        password = os.environ.get("ANYO_UI_PASSWORD", "")
        if not username.strip() or not password.strip():
            raise ValueError("Set nonempty ANYO_UI_USERNAME and ANYO_UI_PASSWORD to enable ui")
        if len(username.encode()) > 512 or len(password.encode()) > 2048:
            raise ValueError("UI credentials exceed supported lengths")
        return cls(username, password)


@dataclass
class _Session:
    csrf: str
    expires: float


class Dashboard:
    def __init__(
        self,
        credentials: UICredentials,
        runner: Callable[[], DurableDispatcher | None],
        *,
        namespace: str,
        max_records: int,
        stale_seconds: float = 60,
    ) -> None:
        self._credentials = credentials
        self._runner = runner
        self._namespace = namespace
        self._max_records = max_records
        self._stale_seconds = stale_seconds
        self._sessions: dict[str, _Session] = {}
        self._attempts: dict[str, tuple[float, int]] = {}

    def routes(self) -> list[Route]:
        return [
            Route("/ui", self.handle),
            Route("/ui/", self.handle),
            Route("/ui/assets/{asset}", self.handle),
            Route("/ui/login", self.handle, methods=["POST"]),
            Route("/ui/logout", self.handle, methods=["POST"]),
            Route("/ui/api/{resource}", self.handle),
            Route("/ui/api/{resource}/{identifier}/{action}", self.handle, methods=["POST"]),
        ]

    async def handle(self, request: Request) -> Response:
        try:
            response = await self._handle(request)
        except (QueueUnavailable, QueueMismatch, CapacityError):
            response = JSONResponse({"error": "Storage unavailable; refresh before retrying."}, 503)
        except (ValueError, TypeError, json.JSONDecodeError):
            response = JSONResponse({"error": "Invalid request."}, 400)
        for name, value in _HEADERS.items():
            response.headers[name] = value
        return response

    def _session(self, request: Request) -> _Session | None:
        now = time.monotonic()
        self._sessions = {k: v for k, v in self._sessions.items() if v.expires > now}
        return self._sessions.get(request.cookies.get(_COOKIE, ""))

    @staticmethod
    def _same_origin(request: Request) -> bool:
        if request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
            return False
        origin = request.headers.get("origin")
        if origin is None:
            return True  # API clients still need JSON plus the session CSRF token.
        parsed = urlsplit(origin)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.scheme == request.url.scheme
            and parsed.netloc == request.url.netloc
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )

    @staticmethod
    async def _body(request: Request) -> dict[str, object]:
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            raise ValueError("Expected JSON")
        data = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(data) + len(chunk) > 4096:
                    raise ValueError("Request too large")
                data.extend(chunk)
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("Expected object")
        return parsed

    async def _login(self, request: Request) -> Response:
        if not self._same_origin(request):
            return JSONResponse({"error": "Request origin rejected."}, 403)
        now = time.monotonic()
        self._attempts = {k: v for k, v in self._attempts.items() if now - v[0] < 60}
        client = request.client.host if request.client else "unknown"
        since, attempts = self._attempts.get(client, (now, 0))
        if attempts >= 10 or sum(v[1] for v in self._attempts.values()) >= 100:
            return JSONResponse({"error": "Too many attempts. Try again in one minute."}, 429)
        self._attempts[client] = (since, attempts + 1)
        try:
            body = await self._body(request)
        except TimeoutError:
            return JSONResponse({"error": "Request timed out."}, 408)
        username, password = body.get("username"), body.get("password")
        valid = isinstance(username, str) and isinstance(password, str)
        if valid:
            assert isinstance(username, str) and isinstance(password, str)
            user_match = hmac.compare_digest(username.encode(), self._credentials.username.encode())
            pass_match = hmac.compare_digest(password.encode(), self._credentials.password.encode())
            valid = user_match and pass_match
        if not valid:
            return JSONResponse({"error": "Incorrect username or password."}, 401)
        self._attempts.pop(client, None)
        self._session(request)  # Remove expired sessions before applying the bound.
        self._sessions.pop(request.cookies.get(_COOKIE, ""), None)
        if len(self._sessions) >= 1024:
            return JSONResponse({"error": "Session capacity reached. Try again later."}, 503)
        token = secrets.token_urlsafe(32)
        self._sessions[token] = _Session(secrets.token_urlsafe(32), now + _SESSION_SECONDS)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            _COOKIE,
            token,
            max_age=_SESSION_SECONDS,
            path="/ui",
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return response

    async def _handle(self, request: Request) -> Response:
        path = request.url.path
        if path == "/ui":
            return RedirectResponse("/ui/", 307)
        if path.startswith("/ui/assets/"):
            name = request.path_params["asset"]
            media = {"ui.js": "text/javascript", "ui.css": "text/css"}
            if name not in media:
                return Response(status_code=404)
            return Response(
                files("anyo.ui").joinpath("assets", name).read_bytes(), media_type=media[name]
            )
        if path == "/ui/login":
            return await self._login(request)
        session = self._session(request)
        if path == "/ui/":
            name = "index.html" if session else "login.html"
            return Response(
                files("anyo.ui").joinpath("assets", name).read_bytes(), media_type="text/html"
            )
        if session is None:
            return JSONResponse({"error": "Sign in to continue."}, 401)
        if request.method == "POST":
            if not self._same_origin(request) or not hmac.compare_digest(
                request.headers.get("x-anyo-csrf", ""), session.csrf
            ):
                return JSONResponse(
                    {"error": "Request verification failed. Refresh and retry."}, 403
                )
        if path == "/ui/logout":
            self._sessions.pop(request.cookies.get(_COOKIE, ""), None)
            response = JSONResponse({"ok": True})
            response.delete_cookie(_COOKIE, path="/ui")
            return response
        resource = request.path_params.get("resource")
        if resource == "session" and request.method == "GET":
            return JSONResponse(
                {
                    "csrf": session.csrf,
                    "namespace": self._namespace,
                    "stale_seconds": self._stale_seconds,
                }
            )
        runner = self._runner()
        backend = runner.operations_backend if runner else None
        if runner is None or backend is None:
            return JSONResponse({"error": "Operations are not available."}, 503)
        if request.method == "POST":
            return await self._action(request, runner, backend)
        if resource == "runs":
            records = await runner.inspect(limit=self._max_records)
            status = request.query_params.get("status")
            if status:
                records = [record for record in records if record.status == status]
            return JSONResponse({"runs": [asdict(record) for record in records]})
        if resource == "workers":
            return JSONResponse({"workers": [asdict(item) for item in await backend.workers()]})
        if resource == "logs":
            after = int(request.query_params.get("after", "0"))
            if after < 0:
                raise ValueError("Invalid cursor")
            page = await backend.logs(
                after=after,
                limit=200,
                run_id=request.query_params.get("run_id") or None,
                worker_id=request.query_params.get("worker_id") or None,
                source=request.query_params.get("source") or None,
                level=request.query_params.get("level") or None,
            )
            return JSONResponse(asdict(page))
        return JSONResponse({"error": "Unknown view."}, 404)

    async def _action(
        self, request: Request, runner: DurableDispatcher, backend: OperationsBackend
    ) -> Response:
        resource = request.path_params["resource"]
        identifier = request.path_params["identifier"]
        action = request.path_params["action"]
        if len(identifier) > 256:
            raise ValueError("Invalid identifier")
        if resource == "runs" and action in {"retry", "cancel", "force_stop"}:
            if action == "retry":
                accepted = await runner.retry(identifier)
            else:
                try:
                    body = await self._body(request)
                except TimeoutError:
                    return JSONResponse({"error": "Request timed out."}, 408)
                expected_token = body.get("expected_token")
                if not isinstance(expected_token, str) or len(expected_token) > 256:
                    raise ValueError("Run cancellation requires its observed attempt token")
                if action == "force_stop":
                    records = await runner.inspect(limit=self._max_records)
                    job = next((item for item in records if item.id == identifier), None)
                    if job is None:
                        return JSONResponse({"error": "Run not found."}, 404)
                    if job.status == "running":
                        workers = await backend.workers()
                        owner = next(
                            (w for w in workers if job.owner and job.owner.startswith(w.id + ":")),
                            None,
                        )
                        if owner is None or owner.execution_mode != "process":
                            return JSONResponse(
                                {"error": "Force stop requires a known worker in process mode."},
                                409,
                            )
                accepted = await backend.cancel(
                    identifier, force=action == "force_stop", expected_token=expected_token
                )
        elif resource == "workers" and action in {"stop", "force_stop"}:
            workers = await backend.workers()
            worker = next((w for w in workers if w.id == identifier), None)
            if worker is None:
                return JSONResponse({"error": "Worker not found."}, 404)
            if action == "force_stop" and worker.execution_mode != "process":
                return JSONResponse({"error": "Force stop requires process mode."}, 409)
            if time.time() - worker.heartbeat_at > self._stale_seconds:
                return JSONResponse(
                    {"error": "Worker is unreachable; termination cannot be confirmed."}, 409
                )
            accepted = await backend.worker_command(identifier, force=action == "force_stop")
        else:
            return JSONResponse({"error": "Unknown operation."}, 404)
        if not accepted:
            return JSONResponse(
                {"error": "State changed or action is not available. Refresh the view."}, 409
            )
        return JSONResponse(
            {"accepted": True, "message": "Request recorded. Watch the state for confirmation."},
            202,
        )
