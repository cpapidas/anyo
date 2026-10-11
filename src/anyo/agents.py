"""Typed harnesses, prompt clients, results, and optional progress observers."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import TracebackType
from typing import Literal, Protocol

from anyo.observability import DiagnosticStream, emit_log, log_scope, register_secrets
from anyo.prompts import JSONValue, PromptRequest, json_text, json_value, load_prompt

__all__ = [
    "Harness",
    "HarnessBuilder",
    "Model",
    "PromptBackend",
    "PromptClient",
    "PromptEvent",
    "PromptFailure",
    "PromptResult",
    "PromptSuccess",
    "PromptFailed",
    "inject_prompt",
    "ainject_prompt",
]
_LOGGER = logging.getLogger(__name__)


async def _finish_cleanup(operation: Awaitable[None]) -> None:
    """Finish owned cleanup even when shutdown delivers repeated cancellation."""
    task = asyncio.ensure_future(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


@dataclass(frozen=True)
class Model:
    model: str
    key: str = field(repr=False)
    base_url: str | None = None


@dataclass(frozen=True)
class PromptSuccess:
    data: JSONValue


@dataclass(frozen=True)
class PromptFailure:
    data: JSONValue
    code: str
    message: str
    origin: Literal["agent", "runtime"]


class PromptFailed(RuntimeError):
    """An explicitly raised unsuccessful prompt result."""

    def __init__(self, result: PromptResult) -> None:
        self.result = result
        super().__init__(result.failure.code if result.failure else "Prompt did not fail")


@dataclass(frozen=True)
class PromptResult:
    run_id: str
    success: PromptSuccess | None = None
    failure: PromptFailure | None = None
    session_id: str | None = None
    message_id: str | None = None

    def __post_init__(self) -> None:
        if (self.success is None) == (self.failure is None):
            raise ValueError("Exactly one of success and failure is required")

    def raise_for_failure(self) -> None:
        if self.failure is not None:
            raise PromptFailed(self)


def runtime_failure(
    run_id: str, code: str, message: str, session_id: str | None = None
) -> PromptResult:
    return PromptResult(
        run_id,
        failure=PromptFailure(None, "anyo." + code, message, "runtime"),
        session_id=session_id,
    )


def parse_result(value: object, run_id: str, session_id: str, message_id: str) -> PromptResult:
    json_text(value)  # Bound size and depth before exposing a copied JSON tree.
    data = json_value(value)
    if not isinstance(data, dict) or set(data) != {
        "type",
        "data",
        "failure_code",
        "failure_message",
    }:
        raise ValueError("Invalid result envelope")
    code, message = data["failure_code"], data["failure_message"]
    if data["type"] == "success" and code is None and message is None:
        return PromptResult(
            run_id, PromptSuccess(data["data"]), session_id=session_id, message_id=message_id
        )
    if (
        data["type"] == "failure"
        and isinstance(code, str)
        and code.strip()
        and not code.startswith("anyo.")
        and isinstance(message, str)
        and message.strip()
    ):
        return PromptResult(
            run_id,
            failure=PromptFailure(data["data"], code, message, "agent"),
            session_id=session_id,
            message_id=message_id,
        )
    raise ValueError("Inconsistent result envelope")


@dataclass(frozen=True)
class PromptEvent:
    run_id: str
    kind: str
    message: str | None = None
    session_id: str | None = None
    message_id: str | None = None
    part_id: str | None = None
    provider_event: str | None = None
    sequence: int = 0
    timestamp: float = field(default_factory=time.time)


class PromptBackend(Protocol):
    async def invoke(
        self, request: PromptRequest, emit: Callable[[PromptEvent], None]
    ) -> PromptResult: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class Harness:
    """Inert configuration. Keys are excluded from repr; do not serialize secrets."""

    provider: str
    models: tuple[Model, ...]
    plugins: tuple[str, ...]
    _factory: Callable[[], PromptBackend] = field(repr=False, compare=False)


class HarnessBuilder(Protocol):
    def __call__(
        self,
        *,
        models: Iterable[Mapping[str, str]],
        plugins: Iterable[str] = (),
        **configuration: object,
    ) -> Harness: ...


class _Progress:
    """Bounded, thread-safe observer buffer, independent of control/result state."""

    def __init__(self) -> None:
        self.items: deque[PromptEvent] = deque()
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.dropped: PromptEvent | None = None
        self.drop_count = 0
        self.sequence = 0
        self.bytes = 0

    def put(self, event: PromptEvent) -> None:
        with self.lock:
            self.sequence += 1
            # Progress is diagnostic, never the authoritative result payload.
            message = event.message[:8192] if event.message else event.message
            event = replace(event, message=message, sequence=self.sequence)
            size = len((message or "").encode("utf-8")) + 256
            while self.items and (len(self.items) >= 256 or self.bytes + size > 1_048_576):
                self.dropped = self.items.popleft()
                self.bytes -= len((self.dropped.message or "").encode("utf-8")) + 256
                self.drop_count += 1
            self.items.append(event)
            self.bytes += size
            self.ready.set()

    def pop(self) -> PromptEvent | None:
        with self.lock:
            if self.dropped is not None:
                event = replace(
                    self.dropped,
                    kind="progress_gap",
                    message=f"Dropped {self.drop_count} progress events",
                )
                self.dropped, self.drop_count = None, 0
                return event
            if not self.items:
                self.ready.clear()
                return None
            event = self.items.popleft()
            self.bytes -= len((event.message or "").encode("utf-8")) + 256
            return event


_current_client: ContextVar[PromptClient | None] = ContextVar("anyo_prompt_client", default=None)


@asynccontextmanager
async def prompt_scope(harness: Harness | None) -> AsyncIterator[None]:
    """Bind application-owned resources before its dispatcher tasks are created."""
    client = PromptClient(harness=harness) if harness is not None else None
    token = _current_client.set(client)
    try:
        if client is not None:
            async with client:
                yield
        else:
            yield
    finally:
        _current_client.reset(token)


class PromptClient:
    """Own an invocation runtime with ``with`` or ``async with``.

    Async clients belong to their entering asyncio loop. Sync clients own a loop
    thread. Returned JSON data is a per-result mutable copy. A client is single-use.
    Sync observers execute on the calling thread and must return promptly.
    Async observers must cooperate with cancellation; their final drain is bounded.
    """

    def __init__(self, *, harness: Harness) -> None:
        self.harness = harness
        self._loop: asyncio.AbstractEventLoop | None = None
        self._backend: PromptBackend | None = None
        self._thread: threading.Thread | None = None
        self._entered = False
        self._closed = False

    async def __aenter__(self) -> PromptClient:
        if self._entered:
            raise RuntimeError("PromptClient is single-use")
        self._entered = True
        self._loop = asyncio.get_running_loop()
        self._backend = self.harness._factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Close the client on its owning loop")
        self._closed = True
        if self._backend:
            await _finish_cleanup(self._backend.close())

    def __enter__(self) -> PromptClient:
        _require_sync()
        if self._entered:
            raise RuntimeError("PromptClient is single-use")
        loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=loop.run_forever, name="anyo-prompts", daemon=True)
        self._thread.start()
        try:
            return asyncio.run_coroutine_threadsafe(self.__aenter__(), loop).result()
        except BaseException:
            loop.call_soon_threadsafe(loop.stop)
            self._thread.join()
            loop.close()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _require_sync()
        assert self._loop is not None and self._thread is not None
        try:
            asyncio.run_coroutine_threadsafe(self.aclose(), self._loop).result()
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._loop.close()

    def _request(
        self,
        prompt_file: str | Path,
        context: object,
        workspace: str | Path | None,
        timeout: float,
        model: str | None,
        include_reasoning: bool,
    ) -> PromptRequest:
        if self._backend is None or self._closed:
            raise RuntimeError("Use PromptClient inside its context manager")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        selected = model or self.harness.models[0].model
        if selected not in {entry.model for entry in self.harness.models}:
            raise ValueError("Model is not registered in this harness")
        directory = Path(workspace or Path.cwd()).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError("workspace must be an existing directory")
        return PromptRequest(
            uuid.uuid4().hex,
            load_prompt(prompt_file, context),
            directory,
            selected,
            timeout,
            include_reasoning,
        )

    async def _invoke(
        self, request: PromptRequest, emit: Callable[[PromptEvent], None]
    ) -> PromptResult:
        """Capture diagnostics independently of the consumer's optional observer."""
        assert self._backend is not None
        register_secrets(model.key for model in self.harness.models)
        source = self.harness.provider
        text_stream = DiagnosticStream(f"{source}.text")
        reasoning_stream = DiagnosticStream(f"{source}.reasoning")

        def diagnostic_json(value: JSONValue) -> str:
            try:
                return json_text(value)
            except Exception:
                # A third-party backend's result remains authoritative even
                # when its diagnostic representation is unavailable or too big.
                return "[output unavailable in diagnostics]"

        def progress(event: PromptEvent) -> None:
            # Delta chunks can split credentials. The operations copy joins
            # logical lines before redaction; observer delivery stays unchanged.
            if event.kind == "text_delta":
                text_stream.feed((event.message or "").encode("utf-8", "replace"))
            elif event.kind == "reasoning_delta":
                if request.include_reasoning:
                    reasoning_stream.feed((event.message or "").encode("utf-8", "replace"))
            elif "reasoning" not in event.kind or request.include_reasoning:
                emit_log(
                    f"{event.kind}: {event.message or ''}",
                    source=f"{source}.progress",
                    prompt_id=request.run_id,
                )
            emit(event)

        with log_scope(prompt_id=request.run_id):
            emit_log(f"Prompt started with model {request.model}", source=f"{source}.prompt")
            try:
                result = await self._backend.invoke(request, progress)
                if result.success is not None:
                    emit_log(
                        f"Prompt outcome: success; data={diagnostic_json(result.success.data)}",
                        source=f"{source}.outcome",
                    )
                else:
                    assert result.failure is not None
                    emit_log(
                        f"Prompt outcome: failure; code={result.failure.code}; "
                        f"message={result.failure.message}; "
                        f"data={diagnostic_json(result.failure.data)}",
                        source=f"{source}.outcome",
                        level="WARNING",
                    )
                return result
            except BaseException as error:
                emit_log(
                    f"Prompt interrupted by {type(error).__name__}",
                    source=f"{source}.outcome",
                    level="WARNING",
                )
                raise
            finally:
                text_stream.feed(b"", final=True)
                reasoning_stream.feed(b"", final=True)

    async def ainject_prompt(
        self,
        prompt_file: str | Path,
        *,
        context: object = None,
        workspace: str | Path | None = None,
        timeout: float = 300,
        model: str | None = None,
        on_event: Callable[[PromptEvent], Awaitable[None]] | None = None,
        include_reasoning: bool = False,
    ) -> PromptResult:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Use the async client on its owning loop")
        request = self._request(prompt_file, context, workspace, timeout, model, include_reasoning)
        progress = _Progress()
        finished = asyncio.Event()

        async def observe() -> None:
            if on_event is None:
                return
            try:
                while True:
                    event = progress.pop()
                    if event is not None:
                        await on_event(event)
                    elif finished.is_set():
                        return
                    else:
                        await asyncio.sleep(0.01)
            except Exception:
                _LOGGER.warning("Prompt observer failed; further notifications disabled")

        observer = asyncio.create_task(observe())
        assert self._backend is not None
        try:
            result = await self._invoke(request, progress.put)
            progress.put(
                PromptEvent(
                    request.run_id,
                    "terminal",
                    "success" if result.success is not None else "failure",
                    result.session_id,
                    result.message_id,
                )
            )
            return result
        finally:
            finished.set()
            try:
                async with asyncio.timeout(0.1):
                    await observer
            except TimeoutError:
                _LOGGER.warning("Prompt observer did not finish before its drain deadline")
            finally:
                if not observer.done():
                    observer.cancel()
                await asyncio.gather(observer, return_exceptions=True)

    def inject_prompt(
        self,
        prompt_file: str | Path,
        *,
        context: object = None,
        workspace: str | Path | None = None,
        timeout: float = 300,
        model: str | None = None,
        on_event: Callable[[PromptEvent], None] | None = None,
        include_reasoning: bool = False,
    ) -> PromptResult:
        _require_sync()
        request = self._request(prompt_file, context, workspace, timeout, model, include_reasoning)
        assert self._loop is not None and self._backend is not None
        progress = _Progress()
        future = asyncio.run_coroutine_threadsafe(self._invoke(request, progress.put), self._loop)
        callback = on_event
        try:
            while not future.done():
                progress.ready.wait(0.05)
                event = progress.pop()
                if event is not None and callback is not None:
                    try:
                        callback(event)
                    except Exception:
                        _LOGGER.warning("Prompt observer failed; further notifications disabled")
                        callback = None
            result = future.result()
            progress.put(
                PromptEvent(
                    request.run_id,
                    "terminal",
                    "success" if result.success is not None else "failure",
                    result.session_id,
                    result.message_id,
                )
            )
            while (event := progress.pop()) is not None and callback is not None:
                try:
                    callback(event)
                except Exception:
                    _LOGGER.warning("Prompt observer failed; further notifications disabled")
                    break
            return result
        except BaseException:
            future.cancel()
            raise


def _require_sync() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("Use ainject_prompt and an async context manager inside an event loop")


def _client() -> PromptClient:
    client = _current_client.get()
    if client is None:
        raise RuntimeError("Configure anyo.harness before start, or use an explicit PromptClient")
    return client


def inject_prompt(
    prompt_file: str | Path,
    *,
    context: object = None,
    workspace: str | Path | None = None,
    timeout: float = 300,
    model: str | None = None,
    on_event: Callable[[PromptEvent], None] | None = None,
    include_reasoning: bool = False,
) -> PromptResult:
    return _client().inject_prompt(
        prompt_file,
        context=context,
        workspace=workspace,
        timeout=timeout,
        model=model,
        on_event=on_event,
        include_reasoning=include_reasoning,
    )


async def ainject_prompt(
    prompt_file: str | Path,
    *,
    context: object = None,
    workspace: str | Path | None = None,
    timeout: float = 300,
    model: str | None = None,
    on_event: Callable[[PromptEvent], Awaitable[None]] | None = None,
    include_reasoning: bool = False,
) -> PromptResult:
    return await _client().ainject_prompt(
        prompt_file,
        context=context,
        workspace=workspace,
        timeout=timeout,
        model=model,
        on_event=on_event,
        include_reasoning=include_reasoning,
    )
