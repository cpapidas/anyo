"""Owned spawn-process execution with an independent parent control boundary.

Only trusted in-memory Python values travel over private local sockets. Nothing
stored in queue payloads is unpickled. POSIX process groups contain ordinary
children; independently detached processes not registered by an adapter are
outside this boundary.
"""

from __future__ import annotations

import asyncio
import inspect
import multiprocessing
import os
import pickle
import signal
import socket
import sys
from collections.abc import Callable, Sequence
from contextvars import copy_context
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from typing import Literal

from anyo.agents import Harness, PromptFailed, prompt_scope
from anyo.execution._operations import LogEntry
from anyo.hooks import HookEvent, Pipeline


@dataclass(frozen=True)
class ProcessResult:
    outcome: Literal["successful", "failed", "interrupted", "cancelled"]
    error: str | None = None
    retry: bool = False


def _safe_error(error: BaseException) -> str:
    name = type(error).__name__
    return name if name.isascii() and name.isidentifier() and len(name) <= 128 else "PipelineError"


def validate_process_configuration(callbacks: Sequence[Pipeline], harness: Harness | None) -> None:
    """Reject nonportable callbacks/configuration before accepting queue work."""
    if os.name != "posix":
        raise ValueError("execution_mode='process' requires Linux or macOS")
    for callback in callbacks:
        if (
            not inspect.isfunction(callback)
            or "<locals>" in callback.__qualname__
            or callback.__name__ == "<lambda>"
        ):
            raise ValueError(
                "Process execution requires importable top-level pipeline functions; "
                "use execution_mode='thread' for closures, lambdas, or callable objects"
            )
        if callback.__module__ == "__main__":
            main_path = getattr(sys.modules["__main__"], "__file__", None)
            if not main_path or main_path.startswith("<"):
                raise ValueError(
                    "Process pipelines require an importable module or a guarded Python file; "
                    "interactive, stdin, and python -c callbacks require thread execution"
                )
        try:
            pickle.dumps(callback)
        except (pickle.PickleError, TypeError, AttributeError):
            raise ValueError(
                "Process pipeline function must be importable by its module name"
            ) from None
    try:
        pickle.dumps(harness)
    except (pickle.PickleError, TypeError, AttributeError):
        raise ValueError(
            "Process execution requires a serializable inert harness configuration"
        ) from None


async def _invoke(callback: Pipeline, event: HookEvent) -> None:
    result: object
    if inspect.iscoroutinefunction(callback):
        result = callback(event)
    else:
        task = asyncio.get_running_loop().run_in_executor(None, copy_context().run, callback, event)
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # The independent supervisor can force-stop this process. A
                # cooperative cancellation must not falsely claim a thread ended.
                cancelled = True
            except BaseException:
                break
        if cancelled:
            try:
                result = task.result()
            except BaseException:
                result = None
            if inspect.iscoroutine(result):
                result.close()
            raise asyncio.CancelledError
        result = task.result()
    if inspect.isawaitable(result):
        await result


def _child(
    callback: Pipeline,
    event: HookEvent,
    harness: Harness | None,
    control: socket.socket,
    logs: socket.socket,
    secrets: tuple[str, ...],
    worker_id: str,
    run_id: str,
    attempt: int,
    capture_logs: bool,
) -> None:
    # Spawn gives this child fresh interpreter/thread state; never fork a
    # threaded receiver. setsid happens before user code or harness execution.
    os.setsid()
    logs.setblocking(False)
    from contextlib import nullcontext

    from anyo.observability import ipc_log_scope, process_scope

    dropped = 0

    def send(value: object) -> None:
        try:
            control.send(pickle.dumps(value))
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Parent loss terminates owned execution rather than leaving a
            # detached pipeline producing unrecorded effects.
            os.killpg(os.getpgrp(), signal.SIGKILL)

    def emit(entry: LogEntry) -> None:
        nonlocal dropped
        if dropped:
            gap = LogEntry(
                f"Dropped {dropped} isolated-process log entries",
                source="logs.gap",
                level="WARNING",
                worker_id=worker_id,
                run_id=run_id,
                attempt=attempt,
            )
            try:
                logs.send(pickle.dumps(gap))
                dropped = 0
            except (BlockingIOError, OSError):
                dropped += 1
                return
        try:
            logs.send(pickle.dumps(entry))
        except (BlockingIOError, OSError):
            dropped += 1

    async def execute() -> ProcessResult:
        task = asyncio.current_task()
        assert task is not None
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        send(("ready", None))
        try:
            async with prompt_scope(harness):
                await _invoke(callback, event)
            return ProcessResult("successful")
        except asyncio.CancelledError:
            return ProcessResult("cancelled", "anyo.cancelled")
        except PromptFailed as error:
            code = error.result.failure.code if error.result.failure else "PromptFailed"
            uncertain = code in {"anyo.outcome_unknown", "anyo.timeout"}
            return ProcessResult(
                "interrupted" if uncertain else "failed",
                code if uncertain else "PromptFailed",
                not uncertain,
            )
        except BaseException as error:
            return ProcessResult(
                "failed" if isinstance(error, Exception) else "interrupted",
                _safe_error(error),
                isinstance(error, Exception),
            )

    scope = (
        ipc_log_scope(emit, secrets=secrets, worker_id=worker_id, run_id=run_id, attempt=attempt)
        if capture_logs
        else nullcontext()
    )
    try:
        with (
            scope,
            process_scope(
                lambda pid: send(("process_started", pid)),
                lambda pid: send(("process_stopped", pid)),
            ),
        ):
            result = asyncio.run(execute())
        # All captured records have reached the socket or counted as dropped.
        send(("result", result))
        if dropped:
            send(("log_gap", dropped))
    finally:
        control.close()
        logs.close()


class IsolatedRun:
    """One owned child and its registered harness groups, never an arbitrary PID."""

    def __init__(
        self,
        callback: Pipeline,
        event: HookEvent,
        harness: Harness | None,
        *,
        worker_id: str,
        run_id: str,
        attempt: int,
        secrets: Sequence[str] = (),
        emit: Callable[[LogEntry], None] | None = None,
    ) -> None:
        self._callback, self._event, self._harness = callback, event, harness
        self._worker_id, self._run_id, self._attempt = worker_id, run_id, attempt
        self._secrets, self._emit = tuple(secrets), emit
        self._process: BaseProcess | None = None
        self._groups: set[int] = set()
        self._cancelled = False
        self._forced = False
        self._group_cleanup_unconfirmed = False
        self._ready = False
        self._signalled = False
        self._result: ProcessResult | None = None

    def cancel(self, *, force: bool = False) -> None:
        self._cancelled = True
        self._forced = self._forced or force
        # Reconcile queued short-lived process start/stop notifications before
        # force-signalling their groups. The independent supervisor polls them.
        if not self._forced:
            self._signal()

    def _signal(self) -> None:
        process = self._process
        if process is None or process.pid is None:
            return
        if self._forced:
            self._kill_groups()
            if process.is_alive():
                process.kill()
        elif self._ready and not self._signalled and process.is_alive():
            self._signalled = True
            process.terminate()

    def _kill_groups(self) -> None:
        process = self._process
        groups = self._groups | ({process.pid} if process is not None and process.pid else set())
        for pid in groups:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # A stale or inaccessible group must not prevent reaping the
                # owned Python process and closing its control channels.
                self._group_cleanup_unconfirmed = True

    def _drain(self, control: socket.socket, logs: socket.socket) -> bool:
        received = False
        for channel, is_log in ((control, False), (logs, True)):
            for _ in range(256):
                try:
                    data = channel.recv(65536)
                except (BlockingIOError, ConnectionResetError):
                    break
                received = True
                value = pickle.loads(data)
                if is_log:
                    if isinstance(value, LogEntry) and self._emit is not None:
                        self._emit(value)
                    continue
                kind, detail = value
                if kind == "ready":
                    self._ready = True
                elif kind == "process_started":
                    self._groups.add(detail)
                elif kind == "process_stopped":
                    self._groups.discard(detail)
                elif kind == "result":
                    self._result = detail
                elif kind == "log_gap" and self._emit is not None:
                    self._emit(
                        LogEntry(
                            f"Dropped {detail} isolated-process log entries",
                            source="logs.gap",
                            level="WARNING",
                            worker_id=self._worker_id,
                            run_id=self._run_id,
                            attempt=self._attempt,
                        )
                    )
        if self._cancelled:
            self._signal()
        return received

    async def run(self) -> ProcessResult:
        # Serialization is local only, before spawning. Queue data uses its
        # existing safe JSON codec; custom events need an importable Python type.
        try:
            pickle.dumps((self._callback, self._event, self._harness))
        except (pickle.PickleError, TypeError, AttributeError):
            return ProcessResult("failed", "anyo.process_serialization")
        parent_control, child_control = socket.socketpair(type=socket.SOCK_DGRAM)
        parent_logs, child_logs = socket.socketpair(type=socket.SOCK_DGRAM)
        parent_control.setblocking(False)
        parent_logs.setblocking(False)
        process = multiprocessing.get_context("spawn").Process(
            target=_child,
            args=(
                self._callback,
                self._event,
                self._harness,
                child_control,
                child_logs,
                self._secrets,
                self._worker_id,
                self._run_id,
                self._attempt,
                self._emit is not None,
            ),
            name=f"anyo-run-{self._run_id[:12]}",
        )
        self._process = process
        try:
            process.start()
            child_control.close()
            child_logs.close()
            if self._cancelled:
                self._signal()
            while process.is_alive():
                self._drain(parent_control, parent_logs)
                await asyncio.sleep(0.02)
            process.join(timeout=0)
            while self._drain(parent_control, parent_logs):
                # No child remains to produce indefinitely. Drain every queued
                # bounded datagram while yielding fairly to receiver/control I/O.
                await asyncio.sleep(0)
            self._kill_groups()
            if self._group_cleanup_unconfirmed:
                return ProcessResult("interrupted", "anyo.process_cleanup_unconfirmed")
            if self._forced:
                if self._emit is not None:
                    self._emit(
                        LogEntry(
                            "Log gap: force stop can discard buffered child output",
                            source="logs.gap",
                            level="WARNING",
                            run_id=self._run_id,
                            worker_id=self._worker_id,
                            attempt=self._attempt,
                        )
                    )
                return ProcessResult("cancelled", "anyo.force_stopped")
            return self._result or ProcessResult("interrupted", "anyo.process_exited")
        finally:
            self._kill_groups()
            if process.pid is not None:
                if process.is_alive():
                    process.kill()
                # SIGKILL makes the wait bounded by OS process teardown; no
                # callback cooperation or child event-loop responsiveness needed.
                await asyncio.to_thread(process.join)
                process.close()
            self._process = None
            parent_control.close()
            parent_logs.close()
            child_control.close()
            child_logs.close()
