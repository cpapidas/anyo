"""A setup-only token collector with no hook registry or execution runtime."""

from __future__ import annotations

import asyncio
import hmac
import math
import os
import socket
import stat
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from anyo.integrations.notion._webhooks import parse_object

MAX_BODY_BYTES = 16384


class _TokenFile:
    """Pin the private directory and exclusively create one complete candidate."""

    def __init__(self, path: Path) -> None:
        if os.name != "posix":
            raise ValueError("Notion setup requires POSIX owner-only file permissions")
        self.name = path.name
        self.directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(self.directory)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("Token directory must be owned by you with mode 0700")
            try:
                os.stat(self.name, dir_fd=self.directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("Token file already exists; choose a new destination")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        os.close(self.directory)

    def write(self, token: bytes) -> None:
        fd = os.open(
            self.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.directory,
        )
        info = os.fstat(fd)
        try:
            os.fchmod(fd, 0o600)
            pending = memoryview(token)
            while pending:
                written = os.write(fd, pending)
                if written <= 0:
                    raise OSError("Unable to save token candidate")
                pending = pending[written:]
            os.fsync(fd)
        except BaseException:
            # Do not remove another file if a local actor replaced our output.
            try:
                current = os.stat(self.name, dir_fd=self.directory, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                    os.unlink(self.name, dir_fd=self.directory)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(fd)


def application(
    token_file: Path,
    *,
    read_timeout: float = 5,
    saved: Callable[[], None] | None = None,
) -> Starlette:
    """Private ASGI factory, separated for deterministic setup-contract tests."""
    target: _TokenFile | None = None
    candidate: bytes | None = None

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        nonlocal target, candidate
        candidate = None
        target = _TokenFile(token_file)
        try:
            yield
        finally:
            target.close()
            target = None
            candidate = None

    async def receive(request: Request) -> Response:
        nonlocal candidate
        if target is None:
            return PlainTextResponse("Setup unavailable", 503)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
            "application/json"
        ):
            return PlainTextResponse("Expected application/json", 415)
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 20:
                return PlainTextResponse("Invalid Content-Length", 400)
            if int(length) > MAX_BODY_BYTES:
                return PlainTextResponse("Setup body too large", 413)
        body = bytearray()
        try:
            async with asyncio.timeout(read_timeout):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        return PlainTextResponse("Setup body too large", 413)
                    body.extend(chunk)
        except TimeoutError:
            return PlainTextResponse("Setup body deadline exceeded", 408)
        except ClientDisconnect:
            return PlainTextResponse("Incomplete setup request", 400)
        try:
            payload = parse_object(bytes(body))
        except (ValueError, UnicodeError, RecursionError):
            return PlainTextResponse("Invalid setup body", 400)
        if "verification_token" not in payload:
            # Never acknowledge normal events during the verification/handoff gap.
            return PlainTextResponse("Setup only; retry after normal receiver starts", 503)
        token = payload.get("verification_token")
        if (
            set(payload) != {"verification_token"}
            or not isinstance(token, str)
            or not 1 <= len(token) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in token)
        ):
            return PlainTextResponse("Invalid verification token envelope", 400)
        encoded = token.encode("ascii")
        if candidate is not None:
            if hmac.compare_digest(candidate, encoded):
                return Response(status_code=200)
            return PlainTextResponse("A different candidate is already saved", 409)
        # No await between exclusive write and state publication: concurrent
        # requests cannot replace the winning candidate. Local files only.
        try:
            target.write(encoded)
        except FileExistsError:
            return PlainTextResponse("Token destination already exists", 409)
        except OSError:
            return PlainTextResponse("Unable to save token candidate", 503)
        candidate = encoded
        if saved is not None:
            saved()
        return Response(status_code=200)

    return Starlette(
        routes=[Route("/webhooks/notion", receive, methods=["POST"])], lifespan=lifespan
    )


async def serve(token_file: Path, *, host: str, port: int, timeout: float) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Setup timeout must be positive and finite")
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")

    candidate_saved = False

    def saved() -> None:
        nonlocal candidate_saved
        candidate_saved = True
        print(
            f"Candidate saved to {token_file}. Confirm it in Notion before using it. "
            "Then stop this helper and start the normal receiver.",
            flush=True,
        )

    app = application(token_file, saved=saved)
    # Prebind ourselves: Uvicorn's bind failure otherwise calls sys.exit.
    with socket.create_server((host, port)) as listener:
        listener.setblocking(False)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                log_config=None,
                access_log=False,
                lifespan="on",
                timeout_graceful_shutdown=5,
            )
        )
        timer = asyncio.get_running_loop().call_later(timeout, setattr, server, "should_exit", True)
        print(
            f"Notion setup listener: {host}:{listener.getsockname()[1]}/webhooks/notion. "
            "Collecting a candidate only; no pipelines will run.",
            flush=True,
        )
        try:
            await server.serve(sockets=[listener])
            if not server.started:
                raise ValueError("Notion setup could not start; check the private destination")
            if not candidate_saved:
                print("Setup stopped without collecting a token candidate.", flush=True)
                raise ValueError("No Notion token candidate collected")
        finally:
            timer.cancel()
