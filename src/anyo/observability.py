"""Opt-in, bounded operations logs without taking over host logging configuration.

Python logging keeps its configured levels and handlers. Python stdout/stderr
writes are captured by line; file-descriptor writes and independently created
threads without copied context cannot be attributed to a pipeline. Diagnostics
are best effort and never determine a pipeline or prompt's authoritative result.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import math
import os
import re
import sys
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from types import TracebackType
from typing import IO, Protocol

__all__ = [
    "LogEntry",
    "OperationsLogSession",
    "accept_log",
    "emit_log",
    "ipc_log_scope",
    "log_scope",
    "process_scope",
    "register_secrets",
]


@dataclass(frozen=True)
class LogEntry:
    """Already-redacted diagnostic; storage assigns a namespace-local cursor."""

    message: str
    source: str = "runtime"
    level: str = "INFO"
    timestamp: float = field(default_factory=time.time)
    run_id: str | None = None
    attempt: int | None = None
    worker_id: str | None = None
    prompt_id: str | None = None
    cursor: int = 0


class LogSink(Protocol):
    async def append_logs(self, entries: Sequence[LogEntry]) -> None: ...


@dataclass(frozen=True)
class _Context:
    run_id: str | None = None
    attempt: int | None = None
    worker_id: str | None = None
    prompt_id: str | None = None


_context: ContextVar[_Context] = ContextVar("anyo_log_context", default=_Context())
_collector: ContextVar[_Collector | None] = ContextVar("anyo_log_collector", default=None)
_process_callbacks: ContextVar[tuple[Callable[[int], None], Callable[[int], None]] | None] = (
    ContextVar("anyo_process_callbacks", default=None)
)
_muted: ContextVar[bool] = ContextVar("anyo_log_muted", default=False)
_private_credentials: ContextVar[tuple[str, ...]] = ContextVar(
    "anyo_private_credentials", default=()
)
_global_lock = threading.RLock()
_collectors: set[_Collector] = set()
_handler: _LogHandler | None = None
_stdout: _Stream | None = None
_stderr: _Stream | None = None


@contextmanager
def log_scope(
    *,
    run_id: str | None = None,
    attempt: int | None = None,
    worker_id: str | None = None,
    prompt_id: str | None = None,
    clear: bool = False,
) -> Iterator[None]:
    """Add correlation for child tasks and threads created with copy_context.

    Unspecified fields inherit the surrounding scope. A prompt ID is distinct
    from the queue run ID, including several prompt calls within one attempt.
    """
    current = _Context() if clear else _context.get()
    token = _context.set(
        _Context(
            run_id if run_id is not None else current.run_id,
            attempt if attempt is not None else current.attempt,
            worker_id if worker_id is not None else current.worker_id,
            prompt_id if prompt_id is not None else current.prompt_id,
        )
    )
    try:
        yield
    finally:
        _context.reset(token)


@contextmanager
def process_scope(
    on_start: Callable[[int], None], on_exit: Callable[[int], None]
) -> Iterator[None]:
    """Report owned process groups to the enclosing execution supervisor."""
    token = _process_callbacks.set((on_start, on_exit))
    try:
        yield
    finally:
        _process_callbacks.reset(token)


def notify_process_started(pid: int) -> None:
    callbacks = _process_callbacks.get()
    if callbacks is not None:
        callbacks[0](pid)


def notify_process_stopped(pid: int) -> None:
    callbacks = _process_callbacks.get()
    if callbacks is not None:
        callbacks[1](pid)


def _targets() -> tuple[_Collector, ...]:
    if _muted.get():
        return ()
    current = _collector.get()
    if current is not None:
        return (current,) if not current.closed else ()
    with _global_lock:
        # A host can embed several apps with different queue namespaces and
        # credentials. An unscoped message has no defensible owner: broadcasting
        # it would leak one application's data into another's log store.
        return tuple(_collectors) if len(_collectors) == 1 else ()


def register_secrets(values: Iterable[str]) -> None:
    """Register configured credentials before a provider can emit diagnostics."""
    secrets = tuple(values)
    for target in _targets():
        target.add_secrets(secrets)


@contextmanager
def private_credential_scope(values: Iterable[str]) -> Iterator[None]:
    """Exclude capability-owned credentials from subsequently started model processes."""
    values = tuple(value for value in values if value)
    register_secrets(values)
    token = _private_credentials.set((*_private_credentials.get(), *values))
    try:
        yield
    finally:
        _private_credentials.reset(token)


def model_environment(environment: dict[str, str]) -> dict[str, str]:
    """Copy an inherited environment without active capability-owned credentials."""
    private = _private_credentials.get()
    return {
        key: value
        for key, value in environment.items()
        if not any(secret in value for secret in private)
    }


def emit_log(
    message: str,
    *,
    source: str = "runtime",
    level: str = "INFO",
    prompt_id: str | None = None,
) -> None:
    """Enqueue a diagnostic synchronously; never wait for storage or a browser."""
    context = _context.get()
    entry = LogEntry(
        message,
        source,
        level,
        run_id=context.run_id,
        attempt=context.attempt,
        worker_id=context.worker_id,
        prompt_id=prompt_id or context.prompt_id,
    )
    for target in _targets():
        target.accept(entry)


def emit_json_log(
    value: object,
    *,
    source: str,
    level: str = "INFO",
    prompt_id: str | None = None,
) -> None:
    """Serialize provider diagnostics without letting formatting change control flow."""
    if not _targets():
        return
    try:
        message = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except Exception:
        message = "Log gap: provider diagnostic could not be serialized"
    emit_log(message, source=source, level=level, prompt_id=prompt_id)


def accept_log(entry: LogEntry) -> None:
    """Accept a child-process diagnostic, applying redaction again locally."""
    for target in _targets():
        target.accept(entry)


class _Collector:
    def __init__(
        self,
        *,
        secrets: Iterable[str] = (),
        worker_id: str | None = None,
        max_entries: int = 512,
        max_entry_bytes: int = 16384,
    ) -> None:
        if type(max_entries) is not int or max_entries < 2:
            raise ValueError("max_entries must be at least two")
        if type(max_entry_bytes) is not int or max_entry_bytes < 1:
            raise ValueError("max_entry_bytes must be positive")
        self.lock = threading.RLock()
        self.entries: deque[LogEntry] = deque()
        self.secrets: tuple[str, ...] = ()
        self.worker_id = worker_id
        self.max_entries = max_entries
        self.max_entry_bytes = max_entry_bytes
        self.dropped = 0
        self.closed = False
        self.partial: dict[tuple[int, str, _Context], str | None] = {}
        self.add_secrets(
            value
            for name, value in os.environ.items()
            if re.search(r"(SECRET|TOKEN|PASSWORD|CREDENTIAL|API_KEY|PRIVATE_KEY)", name, re.I)
        )
        self.add_secrets(secrets)

    def add_secrets(self, values: Iterable[str]) -> None:
        with self.lock:
            values = tuple(values)
            self.secrets = tuple(
                sorted(
                    {
                        *self.secrets,
                        *(value for value in values if value),
                        *(line for value in values for line in value.splitlines() if line),
                    },
                    key=len,
                    reverse=True,
                )
            )

    def redact(self, message: str) -> str:
        for secret in self.secrets:
            message = message.replace(secret, "[redacted]")
        # Defense in depth for credential assignments unknown to the application.
        message = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s\"'<>]+", r"\1 [redacted]", message)
        message = re.sub(
            r"(?i)(\b(?:password|api[_-]?key|access[_-]?token|authorization)\b[\"']?\s*[:=]\s*)"
            r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
            r"\1[redacted]",
            message,
        )
        return re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", message)

    def accept(self, entry: LogEntry) -> None:
        with self.lock:
            if self.closed:
                return
            # Redact the complete string before truncating: cutting first can
            # expose a prefix of a credential at an entry-size boundary.
            message = self.redact(entry.message)
            encoded = message.encode("utf-8", "replace")
            if len(encoded) > self.max_entry_bytes:
                suffix = b"\n[anyo log gap: entry truncated]"
                if self.max_entry_bytes <= len(suffix):
                    message = suffix[: self.max_entry_bytes].decode()
                else:
                    message = (
                        encoded[: self.max_entry_bytes - len(suffix)].decode("utf-8", "ignore")
                        + suffix.decode()
                    )
            entry = replace(
                entry,
                message=message,
                source=self.redact(entry.source)[:512],
                level=self.redact(entry.level)[:512],
                worker_id=entry.worker_id or self.worker_id,
                cursor=0,
            )
            while len(self.entries) >= self.max_entries:
                self.entries.popleft()
                self.dropped += 1
            self.entries.append(entry)

    def take(self, limit: int = 100) -> list[LogEntry]:
        with self.lock:
            result: list[LogEntry] = []
            if self.dropped:
                result.append(
                    LogEntry(
                        f"Log gap: {self.dropped} entries lost to collector capacity "
                        "or storage failure",
                        "anyo.logs",
                        "WARNING",
                        worker_id=self.worker_id,
                    )
                )
                self.dropped = 0
            while self.entries and len(result) < limit:
                result.append(self.entries.popleft())
            return result

    def restore(self, entries: Sequence[LogEntry]) -> None:
        with self.lock:
            self.entries.extendleft(reversed(entries))
            while len(self.entries) > self.max_entries:
                self.entries.popleft()
                self.dropped += 1

    def write(self, text: str, source: str) -> None:
        """Join split writes before redaction. Oversized partial lines are dropped."""
        context = _context.get()
        key = threading.get_ident(), source, context
        with self.lock:
            if key not in self.partial and len(self.partial) >= 256:
                self.partial.pop(next(iter(self.partial)))
                self.dropped += 1
            pending = self.partial.get(key, "")
            for part in text.splitlines(keepends=True):
                if pending is not None:
                    pending += part
                    if len(pending) > 65536:
                        pending = None
                        self.dropped += 1
                if part.endswith(("\n", "\r")):
                    if pending is not None:
                        self.accept(
                            LogEntry(
                                pending.rstrip("\r\n"),
                                source,
                                "ERROR" if source.endswith("stderr") else "INFO",
                                run_id=context.run_id,
                                attempt=context.attempt,
                                worker_id=context.worker_id,
                                prompt_id=context.prompt_id,
                            )
                        )
                    pending = ""
            if pending == "":
                self.partial.pop(key, None)
            else:
                self.partial[key] = pending

    def finish_lines(self) -> None:
        with self.lock:
            for (_, source, context), value in self.partial.items():
                if value:
                    self.accept(
                        LogEntry(
                            value,
                            source,
                            "ERROR" if source.endswith("stderr") else "INFO",
                            run_id=context.run_id,
                            attempt=context.attempt,
                            worker_id=context.worker_id,
                            prompt_id=context.prompt_id,
                        )
                    )
            self.partial.clear()


class _LogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        targets = _targets()
        if not targets:
            return
        token = _muted.set(True)
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + "".join(traceback.format_exception(*record.exc_info))
            context = _context.get()
            entry = LogEntry(
                message,
                record.name,
                record.levelname,
                record.created,
                context.run_id,
                context.attempt,
                context.worker_id,
                context.prompt_id,
            )
            for target in targets:
                target.accept(entry)
        except Exception:
            # A broken user __str__/formatter must not recurse through logging
            # or turn a diagnostic into a pipeline failure.
            for target in targets:
                target.accept(
                    LogEntry("Log gap: could not format a record", "anyo.logs", "WARNING")
                )
        finally:
            _muted.reset(token)


class _BinaryStream:
    def __init__(self, owner: _Stream) -> None:
        self.owner = owner

    def write(self, data: bytes) -> int:
        self.owner.write(data.decode("utf-8", "replace"))
        return len(data)

    def flush(self) -> None:
        self.owner.flush()


class _Stream:
    def __init__(self, wrapped: IO[str], source: str) -> None:
        self.wrapped = wrapped
        self.source = source
        self.buffer = _BinaryStream(self)

    def write(self, text: str) -> int:
        # Preserve the host's output unchanged. Only the operations copy is
        # redacted; the host owns its console and any pre-existing handlers.
        result = self.wrapped.write(text)
        for target in _targets():
            target.write(text, self.source)
        return result

    def flush(self) -> None:
        self.wrapped.flush()

    def __getattr__(self, name: str) -> object:
        return getattr(self.wrapped, name)


def _install(collector: _Collector, capture_output: bool) -> None:
    global _handler, _stdout, _stderr
    with _global_lock:
        _collectors.add(collector)
        if _handler is None:
            _handler = _LogHandler()
            logging.getLogger().addHandler(_handler)
        if capture_output and _stdout is None:
            _stdout = _Stream(sys.stdout, "pipeline.stdout")
            _stderr = _Stream(sys.stderr, "pipeline.stderr")
            sys.stdout = _stdout
            sys.stderr = _stderr


def _uninstall(collector: _Collector) -> None:
    global _handler, _stdout, _stderr
    with _global_lock:
        _collectors.discard(collector)
        if _collectors:
            return
        if _handler is not None:
            logging.getLogger().removeHandler(_handler)
            _handler.close()
            _handler = None
        if _stdout is not None and sys.stdout is _stdout:
            sys.stdout = _stdout.wrapped
        if _stderr is not None and sys.stderr is _stderr:
            sys.stderr = _stderr.wrapped
        _stdout, _stderr = None, None


class OperationsLogSession:
    """Append bounded diagnostic batches to an already-open operations backend.

    Storage failures are retried while the session runs. Close flushes within
    ``flush_timeout``; ``last_error``/``unflushed_count`` expose an incomplete
    shutdown without altering pipeline outcomes or leaking exception messages.
    """

    def __init__(
        self,
        backend: LogSink,
        *,
        namespace: str = "default",
        worker_id: str | None = None,
        secrets: Iterable[str] = (),
        capture_output: bool = True,
        max_entries: int = 512,
        max_entry_bytes: int = 16384,
        flush_timeout: float = 2,
    ) -> None:
        self.backend = backend
        self.namespace = namespace
        self.capture_output = capture_output
        self.collector = _Collector(
            secrets=secrets,
            worker_id=worker_id,
            max_entries=max_entries,
            max_entry_bytes=max_entry_bytes,
        )
        self.flush_timeout = flush_timeout
        if (
            isinstance(flush_timeout, bool)
            or not isinstance(flush_timeout, (int, float))
            or not math.isfinite(flush_timeout)
            or flush_timeout <= 0
        ):
            raise ValueError("flush_timeout must be finite and positive")
        self.last_error: str | None = None
        self.unflushed_count = 0
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._entered = False

    async def __aenter__(self) -> OperationsLogSession:
        if self._entered:
            raise RuntimeError("OperationsLogSession is single-use")
        self._entered = True
        _install(self.collector, self.capture_output)
        self._token = _collector.set(self.collector)
        self._task = asyncio.create_task(self._drain(), name="anyo-logs")
        return self

    def accept(self, entry: LogEntry) -> None:
        self.collector.accept(entry)

    @contextmanager
    def bind(self) -> Iterator[None]:
        """Select this application for callbacks entered outside its startup context.

        Use alongside ``log_scope`` when embedding several applications in one
        process. Unscoped general output is omitted when its owner is ambiguous.
        """
        context_token = None
        if _collector.get() is not self.collector:
            context_token = _context.set(_Context(worker_id=self.collector.worker_id))
        token = _collector.set(self.collector)
        try:
            yield
        finally:
            _collector.reset(token)
            if context_token is not None:
                _context.reset(context_token)

    async def _drain(self) -> None:
        token = _muted.set(True)
        try:
            while True:
                entries = self.collector.take()
                if entries:
                    try:
                        await self.backend.append_logs(entries)
                        self.last_error = None
                    except Exception as error:
                        self.last_error = type(error).__name__
                        self.collector.restore(entries)
                    except asyncio.CancelledError:
                        self.collector.restore(entries)
                        raise
                if self._stop.is_set() and not self.collector.entries:
                    return
                await asyncio.sleep(0.05)
        finally:
            _muted.reset(token)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.collector.finish_lines()
        _uninstall(self.collector)
        self.collector.closed = True
        try:
            _collector.reset(self._token)
        except ValueError:
            # A dispatcher can enter on startup and close on an owned shutdown
            # task. Its inherited references are inert after collector.closed.
            if _collector.get() is self.collector:
                _collector.set(None)
        self._stop.set()
        if self._task is not None:
            try:
                async with asyncio.timeout(self.flush_timeout):
                    await self._task
            except TimeoutError:
                self.last_error = self.last_error or "FlushTimeout"
            finally:
                if not self._task.done():
                    self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self.unflushed_count = len(self.collector.entries) + self.collector.dropped


@contextmanager
def ipc_log_scope(
    emit: Callable[[LogEntry], None],
    *,
    secrets: Iterable[str] = (),
    run_id: str | None = None,
    attempt: int | None = None,
    worker_id: str | None = None,
    max_entry_bytes: int = 16384,
) -> Iterator[None]:
    """Capture isolated-child logs through a bounded nonblocking IPC emitter.

    ``emit`` must return promptly or raise when full. It is invoked on a
    diagnostic thread and must not write to logging/stdout itself.
    """
    collector = _Collector(secrets=secrets, worker_id=worker_id, max_entry_bytes=max_entry_bytes)
    stop = threading.Event()

    def drain() -> None:
        while True:
            batch = collector.take()
            for entry in batch:
                try:
                    emit(entry)
                except Exception:
                    with collector.lock:
                        collector.dropped += 1
            if stop.is_set() and not collector.entries:
                return
            stop.wait(0.02)

    _install(collector, True)
    token = _collector.set(collector)
    thread = threading.Thread(target=drain, name="anyo-log-ipc", daemon=True)
    thread.start()
    try:
        with log_scope(run_id=run_id, attempt=attempt, worker_id=worker_id):
            yield
    finally:
        collector.finish_lines()
        _uninstall(collector)
        collector.closed = True
        _collector.reset(token)
        stop.set()
        thread.join(timeout=2)


class DiagnosticStream:
    """Bounded incremental UTF-8 lines for an owned harness's raw diagnostics."""

    def __init__(self, source: str, *, level: str = "INFO") -> None:
        self.source, self.level = source, level
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.pending: str | None = ""

    def feed(self, data: bytes, *, final: bool = False) -> None:
        text = self.decoder.decode(data, final=final)
        for part in text.splitlines(keepends=True):
            if self.pending is not None:
                self.pending += part
                if len(self.pending) > 65536:
                    emit_log(
                        "Log gap: oversized diagnostic line", source=self.source, level="WARNING"
                    )
                    self.pending = None
            if part.endswith(("\n", "\r")):
                if self.pending is not None:
                    emit_log(self.pending.rstrip("\r\n"), source=self.source, level=self.level)
                self.pending = ""
        if final and self.pending:
            emit_log(self.pending, source=self.source, level=self.level)
            self.pending = ""
