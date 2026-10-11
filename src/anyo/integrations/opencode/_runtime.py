"""Bounded HTTP/SSE execution against the pinned OpenCode protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx2

from anyo.agents import PromptEvent, PromptResult, _finish_cleanup, parse_result, runtime_failure
from anyo.integrations.opencode._configuration import VERSION, Settings
from anyo.integrations.opencode._process import OpenCodeError, ServerProcess, native_config
from anyo.observability import emit_json_log
from anyo.prompts import (
    MAX_BYTES,
    JSONValue,
    PromptRequest,
    decode_json,
    envelope_instruction,
    envelope_schema,
)


def _object(value: JSONValue) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        raise OpenCodeError("protocol_error", "OpenCode returned an unexpected object")
    return value


def _id(value: JSONValue) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for c in value
        )
    ):
        raise OpenCodeError("protocol_error", "OpenCode returned an invalid identifier")
    return value


@dataclass
class _Run:
    request: PromptRequest
    emit: Callable[[PromptEvent], None]
    session: str = ""
    user: str | None = None
    connected: asyncio.Event = field(default_factory=asyncio.Event)
    error: OpenCodeError | None = None
    parts: dict[str, tuple[str, str]] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    secrets: tuple[str, ...] = field(default=(), repr=False)

    def event(
        self,
        kind: str,
        message: str | None = None,
        *,
        message_id: str | None = None,
        part_id: str | None = None,
        native: str | None = None,
    ) -> None:
        if message:
            for secret in self.secrets:
                message = message.replace(secret, "[redacted]")
        self.emit(
            PromptEvent(
                self.request.run_id,
                kind,
                message,
                self.session or None,
                message_id,
                part_id,
                native,
            )
        )

    def update(self, data: JSONValue) -> None:
        event = _object(data)
        name = event.get("type")
        if name == "server.connected":
            self.connected.set()
            return
        props = event.get("properties")
        if not isinstance(props, dict):
            return
        event_id = event.get("id")
        if isinstance(event_id, str):
            if event_id in self.seen:
                return
            if len(self.seen) >= 2048:
                self.seen.clear()
            self.seen.add(event_id)
        sid = props.get("sessionID")
        part = props.get("part")
        info = props.get("info")
        if sid is None and isinstance(part, dict):
            sid = part.get("sessionID")
        if sid is None and isinstance(info, dict):
            sid = info.get("sessionID")
        if name == "session.error" and sid is None:
            self.error = OpenCodeError(
                "configuration_failed", "OpenCode reported an initialization error"
            )
            return
        if sid != self.session:
            return
        native = str(name)
        if name in {"permission.asked", "question.asked"}:
            self.error = OpenCodeError(
                "interaction_required", "OpenCode requires unattended interaction"
            )
            self.event("status", "interaction_required", native=native)
        elif name == "session.error":
            self.error = OpenCodeError("provider_error", "OpenCode reported a session error")
        elif name == "session.status":
            status = props.get("status")
            if isinstance(status, dict) and isinstance(status.get("type"), str):
                self.event("status", str(status["type"]), native=native)
        elif name == "message.updated" and isinstance(info, dict):
            if info.get("role") == "user":
                self.user = _id(info.get("id"))
        elif name == "message.part.updated" and isinstance(part, dict):
            mid, pid = _id(part.get("messageID")), _id(part.get("id"))
            if mid == self.user:
                return
            kind = part.get("type")
            if kind in {"text", "reasoning"}:
                text = part.get("text")
                if not isinstance(text, str):
                    return
                previous = self.parts.get(pid, ("", ""))[1]
                self.parts[pid] = (str(kind), text[-8192:])
                if kind == "reasoning" and not self.request.include_reasoning:
                    return
                # Snapshot content is marked separately from deltas for consumers.
                if text[-8192:] != previous:
                    self.event(
                        "assistant_text" if kind == "text" else "reasoning",
                        text,
                        message_id=mid,
                        part_id=pid,
                        native=native,
                    )
            elif kind == "tool":
                state = part.get("state")
                if isinstance(state, dict):
                    # Keep tool input/output in the opt-in operations collector,
                    # independently of the existing lightweight observer events.
                    emit_json_log(
                        {"tool": part.get("tool"), "state": state},
                        source="opencode.tools",
                        prompt_id=self.request.run_id,
                    )
                    status = state.get("status")
                    prior = self.parts.get(pid)
                    self.parts[pid] = ("tool", str(status))
                    if prior == ("tool", str(status)):
                        return
                    mapped = {
                        "pending": "tool_start",
                        "running": "tool_update",
                        "completed": "tool_end",
                        "error": "tool_end",
                    }.get(str(status))
                    if mapped:
                        self.event(
                            mapped,
                            f"{part.get('tool', 'tool')}: {status}",
                            message_id=mid,
                            part_id=pid,
                            native=native,
                        )
            if len(self.parts) > 1024:
                self.parts.pop(next(iter(self.parts)))
        elif name == "message.part.delta":
            mid, pid = _id(props.get("messageID")), _id(props.get("partID"))
            # After a stream gap we may not know whether a delta is reasoning.
            # Never misclassify unknown content as ordinary assistant text.
            if pid not in self.parts:
                return
            kind, previous = self.parts[pid]
            delta = props.get("delta")
            if mid == self.user or props.get("field") != "text" or not isinstance(delta, str):
                return
            self.parts[pid] = (kind, (previous + delta)[-8192:])
            if kind == "reasoning" and not self.request.include_reasoning:
                return
            self.event(
                "reasoning_delta" if kind == "reasoning" else "text_delta",
                delta,
                message_id=mid,
                part_id=pid,
                native=native,
            )


class OpenCodeBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.server = ServerProcess(settings)
        self.http: httpx2.AsyncClient | None = None
        self.lock = asyncio.Lock()
        self.active: set[asyncio.Task[object]] = set()
        self.closed = False

    async def _ready(self) -> tuple[httpx2.AsyncClient, ServerProcess]:
        async with self.lock:
            if self.http is not None and self.server.alive:
                return self.http, self.server
            if self.http:
                await self.http.aclose()
                self.http = None
            await self.server.close()
            self.server = ServerProcess(self.settings)
            url = await self.server.start()
            client = httpx2.AsyncClient(
                base_url=url,
                auth=(self.settings.server_username, self.server.password),
                timeout=self.settings.control_timeout,
                trust_env=False,
                follow_redirects=False,
                limits=httpx2.Limits(max_connections=self.settings.max_concurrency * 4 + 4),
            )
            try:
                health = _object(await self._request(client, "GET", "/global/health"))
                if health.get("healthy") is not True or health.get("version") != VERSION:
                    raise OpenCodeError("version_mismatch", f"OpenCode {VERSION} is required")
            except BaseException:
                await client.aclose()
                await self.server.close()
                raise
            self.http = client
            return client, self.server

    async def _request(
        self,
        client: httpx2.AsyncClient,
        method: str,
        path: str,
        run: _Run | None = None,
        body: JSONValue = None,
        *,
        pending_format: bool = False,
    ) -> JSONValue:
        headers = self._headers(run)
        try:
            async with (
                asyncio.timeout(self.settings.control_timeout),
                client.stream(method, path, headers=headers, json=body) as response,
            ):
                if response.status_code in {401, 403}:
                    raise OpenCodeError(
                        "authentication_failed", "OpenCode authentication was rejected"
                    )
                if response.status_code not in {200, 201, 204, 400}:
                    raise OpenCodeError("protocol_error", "Unexpected OpenCode HTTP status")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise OpenCodeError("protocol_error", "OpenCode response exceeds 1 MiB")
                value = decode_json(bytes(data)) if data else None
                if response.status_code == 400:
                    details = value.get("data") if isinstance(value, dict) else None
                    message = details.get("message") if isinstance(details, dict) else None
                    # v1.18.34 cannot encode a persisted user message with a JSON
                    # output format. Read only the latest message, and tolerate
                    # this exact upstream error until an assistant message exists.
                    if (
                        pending_format
                        and isinstance(message, str)
                        and "Expected OutputFormatJsonSchema" in message
                        and '["info"]["format"]' in message
                    ):
                        return None
                    raise OpenCodeError("request_rejected", "OpenCode rejected the request")
                return value
        except (httpx2.HTTPError, TimeoutError):
            raise OpenCodeError("connection_lost", "Could not communicate with OpenCode") from None
        except ValueError:
            raise OpenCodeError("protocol_error", "OpenCode returned invalid JSON") from None

    @staticmethod
    def _headers(run: _Run | None) -> dict[str, str]:
        return {"x-opencode-directory": quote(str(run.request.workspace), safe="")} if run else {}

    async def _events(self, client: httpx2.AsyncClient, run: _Run) -> None:
        reconnect = False
        delay = 0.1
        while True:
            try:
                async with client.stream(
                    "GET",
                    "/event",
                    headers=self._headers(run),
                    timeout=httpx2.Timeout(self.settings.control_timeout, read=30),
                ) as response:
                    if response.status_code != 200:
                        raise OpenCodeError("connection_lost", "OpenCode event subscription failed")
                    pending = b""
                    lines: list[bytes] = []
                    frame_size = 0
                    async for chunk in response.aiter_bytes():
                        pending += chunk
                        if len(pending) > MAX_BYTES:
                            raise OpenCodeError("protocol_error", "OpenCode event exceeds 1 MiB")
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            line = line.rstrip(b"\r")
                            if line.startswith(b"data:"):
                                value = line[5:].lstrip(b" ")
                                frame_size += len(value)
                                if frame_size > MAX_BYTES:
                                    raise OpenCodeError(
                                        "protocol_error", "OpenCode event exceeds 1 MiB"
                                    )
                                lines.append(value)
                            elif not line and lines:
                                run.update(decode_json(b"\n".join(lines)))
                                lines, frame_size = [], 0
                                delay = 0.1
                                if reconnect:
                                    reconnect = False
                                    run.event(
                                        "progress_gap",
                                        "Reconnected; missed progress may be unavailable",
                                    )
            except (httpx2.HTTPError, OpenCodeError, ValueError):
                if not run.connected.is_set():
                    run.error = OpenCodeError(
                        "connection_lost", "OpenCode event subscription failed"
                    )
                    run.connected.set()
                    return
            reconnect = True
            run.event("progress_gap", "OpenCode event stream disconnected; reconciling messages")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 1)

    async def _configuration(self, client: httpx2.AsyncClient, run: _Run) -> None:
        actual = _object(await self._request(client, "GET", "/config", run))
        expected = native_config(self.settings)
        for key in (
            "model",
            "small_model",
            "plugin",
            "enabled_providers",
            "share",
            "autoupdate",
            "permission",
            "default_agent",
            "snapshot",
        ):
            if actual.get(key) != expected[key]:
                raise OpenCodeError(
                    "configuration_mismatch", "OpenCode configuration differs from the harness"
                )
        providers = _object(actual.get("provider"))
        for model in self.settings.models:
            entry = _object(providers.get(model.model.split("/", 1)[0]))
            options = _object(entry.get("options"))
            if options.get("apiKey") != model.key or (
                model.base_url and options.get("baseURL") != model.base_url
            ):
                raise OpenCodeError(
                    "configuration_mismatch", "OpenCode credentials differ from the harness"
                )

    async def _reconcile(self, client: httpx2.AsyncClient, run: _Run) -> PromptResult | None:
        messages = await self._request(
            client, "GET", f"/session/{run.session}/message?limit=1", run, pending_format=True
        )
        if messages is None:
            return None
        if not isinstance(messages, list):
            raise OpenCodeError("protocol_error", "OpenCode returned invalid session messages")
        infos = [_object(_object(m).get("info")) for m in messages]
        users = [m for m in infos if m.get("role") == "user"]
        if len(users) > 1:
            raise OpenCodeError("protocol_error", "Unexpected extra prompt in an owned session")
        if users:
            run.user = _id(users[0].get("id"))
        elif run.user is None and infos:
            # The session is fresh and receives exactly one POST. The latest
            # assistant's parent recovers the server-generated user ID after a
            # stream gap, without fetching the broken user-message encoding.
            if infos[-1].get("role") == "assistant":
                run.user = _id(infos[-1].get("parentID"))
        statuses = _object(await self._request(client, "GET", "/session/status", run))
        state = statuses.get(run.session)
        idle = state is None or (isinstance(state, dict) and state.get("type") == "idle")
        candidates = [
            m
            for m in infos
            if m.get("role") == "assistant"
            and run.user is not None
            and m.get("parentID") == run.user
            and not m.get("summary")
        ]

        # Server messages are ordered, but completion timestamps also guard against
        # alternate transport ordering without relying on opaque lexical IDs.
        def created(info: dict[str, JSONValue]) -> float:
            stamp = info.get("time")
            value = stamp.get("created", 0) if isinstance(stamp, dict) else 0
            return float(value) if isinstance(value, (float, int)) else 0

        candidates.sort(key=created)
        if idle and candidates:
            final = candidates[-1]
            if final.get("error") is not None:
                error = final["error"]
                code = (
                    "invalid_output"
                    if isinstance(error, dict) and error.get("name") == "StructuredOutputError"
                    else "provider_error"
                )
                raise OpenCodeError(code, "OpenCode could not complete the requested output")
            stamp = final.get("time")
            if isinstance(stamp, dict) and stamp.get("completed") is not None:
                try:
                    return parse_result(
                        final.get("structured"),
                        run.request.run_id,
                        run.session,
                        _id(final.get("id")),
                    )
                except ValueError:
                    raise OpenCodeError(
                        "invalid_output", "OpenCode returned no valid result envelope"
                    ) from None
        # Permissions/questions may have arrived while the event stream was down.
        for path in ("/permission", "/question"):
            requests = await self._request(client, "GET", path, run)
            if isinstance(requests, list) and any(
                isinstance(r, dict) and r.get("sessionID") == run.session for r in requests
            ):
                raise OpenCodeError(
                    "interaction_required", "OpenCode requires unattended interaction"
                )
        return None

    async def invoke(
        self, request: PromptRequest, emit: Callable[[PromptEvent], None]
    ) -> PromptResult:
        if self.closed:
            raise RuntimeError("OpenCode client is closed")
        if len(self.active) >= self.settings.max_concurrency:
            return runtime_failure(
                request.run_id, "capacity_exceeded", "Prompt capacity is exhausted"
            )
        task = asyncio.current_task()
        assert task is not None
        self.active.add(task)
        run = _Run(request, emit, secrets=tuple(m.key for m in self.settings.models))
        stream: asyncio.Task[None] | None = None
        client: httpx2.AsyncClient | None = None
        server: ServerProcess | None = None
        complete = False
        submitted = False
        uncertain = False
        try:
            async with asyncio.timeout(request.timeout):
                client, server = await self._ready()
                run.secrets += (server.password,)
                await self._configuration(client, run)
                session = _object(
                    await self._request(
                        client,
                        "POST",
                        "/session",
                        run,
                        {
                            "title": f"Anyo {request.run_id}",
                            "permission": [
                                {"permission": key, "pattern": "*", "action": action}
                                for key, action in self.settings.permissions
                            ],
                        },
                    )
                )
                run.session = _id(session.get("id"))
                stream = asyncio.create_task(self._events(client, run))
                await run.connected.wait()
                if run.error:
                    raise run.error
                # Tool discovery initializes configured plugins before the model call.
                await self._request(client, "GET", "/experimental/tool/ids", run)
                if server.plugin_failed or run.error:
                    raise OpenCodeError(
                        "configuration_failed", "An OpenCode plugin could not initialize"
                    )
                provider, model = request.model.split("/", 1)
                submitted = True
                try:
                    await self._request(
                        client,
                        "POST",
                        f"/session/{run.session}/prompt_async",
                        run,
                        {
                            "parts": [{"type": "text", "text": request.text}],
                            "system": envelope_instruction(),
                            "agent": "anyo",
                            "model": {"providerID": provider, "modelID": model},
                            "format": {
                                "type": "json_schema",
                                "schema": envelope_schema(),
                                "retryCount": 0,
                            },
                        },
                    )
                except OpenCodeError as error:
                    if error.code != "connection_lost":
                        raise
                    uncertain = True  # Never repeat an ambiguously accepted POST.
                run.event("status", "submitted")
                while True:
                    if not server.alive:
                        raise OpenCodeError("server_exited", "OpenCode exited during the prompt")
                    if server.plugin_failed:
                        raise OpenCodeError("configuration_failed", "An OpenCode plugin failed")
                    if run.error:
                        raise run.error
                    try:
                        result = await self._reconcile(client, run)
                    except OpenCodeError as error:
                        if error.code != "connection_lost":
                            raise
                        run.event("progress_gap", "Reconciliation interrupted; retrying reads")
                    else:
                        if result is not None:
                            complete = True
                            return result
                    await asyncio.sleep(self.settings.poll_interval)
        except TimeoutError:
            return runtime_failure(
                request.run_id,
                "outcome_unknown" if uncertain else "timeout",
                "Prompt deadline expired; external effects may have occurred",
                run.session or None,
            )
        except OpenCodeError as error:
            return runtime_failure(request.run_id, error.code, error.message, run.session or None)
        except OSError:
            return runtime_failure(request.run_id, "startup_failed", "Could not start OpenCode")
        finally:

            async def cleanup() -> None:
                if stream:
                    stream.cancel()
                    await asyncio.gather(stream, return_exceptions=True)
                if client is not None and run.session:
                    try:
                        if submitted and not complete:
                            await self._request(
                                client, "POST", f"/session/{run.session}/abort", run, {}
                            )
                        await self._request(client, "DELETE", f"/session/{run.session}", run)
                    except OpenCodeError:
                        if server is not None and not self.settings.server_url:
                            # An unconfirmed abort must not leave owned tools running.
                            await server.close()

            try:
                await _finish_cleanup(cleanup())
            finally:
                self.active.discard(task)

    async def close(self) -> None:
        self.closed = True
        tasks = list(self.active)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.http:
            await self.http.aclose()
            self.http = None
        await self.server.close()
