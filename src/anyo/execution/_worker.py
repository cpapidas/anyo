"""Pull-based workers and asynchronous admission over the durable queue contract."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import socket
import time
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, nullcontext
from contextvars import copy_context
from dataclasses import dataclass
from typing import Literal

from anyo.agents import Harness, PromptFailed
from anyo.execution._codec import EventCodecRegistry
from anyo.execution._contracts import (
    CapacityError,
    Claim,
    Job,
    JobInfo,
    Outcome,
    QueueBackend,
    QueueMismatch,
    QueueSettings,
    QueueUnavailable,
)
from anyo.execution._identity import fingerprint, registry, resolve_key
from anyo.execution._operations import (
    OperationsBackend,
    OperationsSettings,
    RunControl,
    WorkerInfo,
)
from anyo.execution._process import IsolatedRun, ProcessResult, validate_process_configuration
from anyo.hooks import Hook, HookEvent
from anyo.observability import OperationsLogSession, emit_log, log_scope

_LOGGER = logging.getLogger(__name__)


def _error_name(error: BaseException) -> str:
    name = type(error).__name__
    return name if name.isascii() and name.isidentifier() and len(name) <= 128 else "PipelineError"


@dataclass
class _Attempt:
    claim: Claim
    task: asyncio.Task[BaseException | None] | None = None
    process: IsolatedRun | None = None
    command: RunControl | None = None


class DurableDispatcher:
    """Own a queue connection and, optionally, local workers.

    The caller establishes prompt context before ``start``. Calls belong to the
    starting event loop. Normal close stops claiming and finishes owned attempts;
    it deliberately leaves unclaimed backlog for another worker. ``wait_idle``
    scans retained namespace records and waits for all queued/running work,
    including delayed retries; another producer can add work after it returns.
    """

    def __init__(
        self,
        backend: QueueBackend,
        hooks: Sequence[Hook],
        codecs: EventCodecRegistry,
        *,
        workers: int = 4,
        run_workers: bool = True,
        lease_seconds: float = 30,
        poll_interval: float = 0.1,
        operations: OperationsSettings | None = None,
        execution_mode: Literal["thread", "process"] = "thread",
        harness: Harness | None = None,
        namespace: str = "default",
        redaction_secrets: Sequence[str] = (),
    ) -> None:
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be a positive integer")
        if type(run_workers) is not bool:
            raise TypeError("run_workers must be a boolean")
        QueueSettings(lease_seconds=lease_seconds, poll_interval=poll_interval)
        if execution_mode not in {"thread", "process"}:
            raise ValueError("execution_mode must be 'thread' or 'process'")
        if operations is not None and not isinstance(backend, OperationsBackend):
            raise ValueError("This queue backend does not support operations")
        self._backend = backend
        self._hooks = tuple(hooks)
        self._codecs = codecs
        self._worker_count = workers if run_workers else 0
        self._lease_seconds = lease_seconds
        self._poll_interval = poll_interval
        self._registered: dict[str, str] = {}
        self._by_key: dict[str, Hook] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._closed = False
        self._accepting = False
        self._failure: BaseException | None = None
        self._failure_ready = asyncio.Event()
        self._shutdown_task: asyncio.Task[None] | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._operations = backend if operations is not None else None
        self._operations_settings = operations
        self._execution_mode = execution_mode
        self._harness = harness
        self._namespace = namespace
        self._redaction_secrets = tuple(redaction_secrets)
        self._worker_id = uuid.uuid4().hex
        self._started_at = 0.0
        self._active: dict[str, _Attempt] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._supervisor_done = asyncio.Event()
        self._stopped = asyncio.Event()
        self._logs: OperationsLogSession | None = None

    @property
    def operations_backend(self) -> OperationsBackend | None:
        """Return the explicitly enabled operations store, if available."""
        if isinstance(self._operations, OperationsBackend):
            return self._operations
        return None

    @property
    def worker_id(self) -> str:
        """Stable identity for this dispatcher lifetime, never a caller-supplied PID."""
        return self._worker_id

    def log_context(self) -> AbstractContextManager[None]:
        """Bind this runtime's log collector to a receiver/request task.

        Consumers hosting several applications in one interpreter can use this
        context to route otherwise unscoped diagnostics to their owning runtime.
        It is inert when operations are disabled or the dispatcher is closed.
        """
        if self._logs is not None and not self._closed:
            return self._logs.bind()
        return nullcontext()

    async def start(self) -> None:
        """Validate the complete registry, open storage, and start local workers."""
        if self._started or self._closed:
            raise RuntimeError("A durable dispatcher can only be started once")
        self._started = True
        self._loop = asyncio.get_running_loop()
        self._registered = registry(self._hooks)
        self._by_key = {
            resolve_key(hook): hook for hook in self._hooks if hook.pipeline is not None
        }
        try:
            if self._execution_mode == "process":
                validate_process_configuration(
                    [hook.pipeline for hook in self._hooks if hook.pipeline is not None],
                    self._harness,
                )
            operations = self.operations_backend
            if operations is not None:
                assert self._operations_settings is not None
                operations.configure_operations(self._operations_settings)
            await self._backend.open(self._registered)
            if operations is not None:
                assert self._operations_settings is not None
                self._logs = OperationsLogSession(
                    operations,
                    namespace=self._namespace,
                    worker_id=self._worker_id if self._worker_count else None,
                    secrets=self._redaction_secrets,
                    max_entry_bytes=self._operations_settings.max_log_bytes,
                )
                await self._logs.__aenter__()
            self._started_at = time.time()
            if operations is not None and self._worker_count:
                await self._worker_heartbeat()
        except BaseException:
            try:
                if self._logs is not None:
                    await self._logs.__aexit__(None, None, None)
            finally:
                await self._backend.close()
                self._closed = True
            raise
        self._accepting = True
        if self._worker_count and self._execution_mode == "thread":
            self._executor = ThreadPoolExecutor(
                max_workers=self._worker_count, thread_name_prefix="anyo-pipeline"
            )
        if operations is not None and self._worker_count:
            self._supervisor = asyncio.create_task(self._supervise(), name="anyo-operations")
        self._workers = [
            asyncio.create_task(
                self._worker(f"{self._worker_id}:{index}"), name=f"anyo-worker-{index}"
            )
            for index in range(self._worker_count)
        ]

    async def wait_stopped(self) -> None:
        """Wait until a control command drains or force-stops local execution."""
        self._check_loop()
        await self._stopped.wait()
        self._raise_failure()

    async def submit(
        self, jobs: Sequence[tuple[Hook, HookEvent]], *, deadline: float | None = None
    ) -> int:
        """Serialize the entire fan-out before atomically admitting it.

        A timeout can race a committed write. The deterministic job identities
        allow safe redelivery after an ambiguous storage response.
        """
        self._check_loop()
        if not self._accepting:
            raise CapacityError("The dispatcher is not accepting deliveries")
        serialized = []
        for hook, event in jobs:
            if hook.pipeline is None:
                raise ValueError("A background job requires a pipeline")
            key = resolve_key(hook)
            signature = fingerprint(hook)
            if self._registered.get(key) != signature:
                raise QueueMismatch("Hook does not match the running registry")
            identity = json.dumps(
                [key, signature, event.provider, event.delivery_id],
                ensure_ascii=True,
                separators=(",", ":"),
            )
            serialized.append(
                Job(
                    id=hashlib.sha256(identity.encode()).hexdigest(),
                    hook_id=key,
                    fingerprint=signature,
                    event=self._codecs.encode(event),
                    max_attempts=hook.retry.max_attempts,
                    retry_delay=hook.retry.delay,
                    retry_interrupted=hook.retry.retry_interrupted,
                )
            )
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Delivery admission deadline exceeded")
        # Admission has no worker lease to abandon. A failed/ambiguous write is
        # retryable by delivery identity; do not permanently disable a receiver.
        async with asyncio.timeout_at(deadline):
            return await self._backend.admit(serialized)

    async def inspect(self, *, limit: int = 100) -> list[JobInfo]:
        """Return safe namespace job summaries, including terminal outcomes."""
        self._check_loop()
        return await self._backend.inspect(limit=limit)

    async def retry(self, job_id: str) -> bool:
        """Explicitly retry a failed/interrupted job under shared capacity."""
        self._check_loop()
        if not self._accepting:
            raise CapacityError("The dispatcher is not accepting deliveries")
        return await self._backend.retry(job_id)

    async def wait_idle(self) -> None:
        """Wait for no pending namespace jobs, propagating a fatal local failure."""
        self._check_loop()
        while True:
            self._raise_failure()
            records = await self._backend.inspect(limit=2**31 - 1)
            if not any(record.status in {"queued", "running"} for record in records):
                self._raise_failure()
                return
            if self._stop.is_set():
                raise RuntimeError("Dispatcher stopped with pending jobs")
            await self._pause(self._poll_interval)

    async def wait_failure(self) -> None:
        """Block until local worker/admission ownership fails, then raise it."""
        self._check_loop()
        await self._failure_ready.wait()
        self._raise_failure()

    async def close(self) -> None:
        """Stop claims and finish owned callbacks before releasing storage.

        Cancellation of the caller waits for cleanup, including synchronous
        callback threads, before propagating. No arbitrary callback is killed.
        """
        if not self._started:
            self._closed = True
            return
        if self._loop is not asyncio.get_running_loop():
            raise RuntimeError("Use the asyncio loop that started the dispatcher")
        self._accepting = False
        self._stop.set()
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        cancelled = False
        while not self._shutdown_task.done():
            try:
                await asyncio.shield(self._shutdown_task)
            except asyncio.CancelledError:
                cancelled = True
        self._shutdown_task.result()
        if cancelled:
            raise asyncio.CancelledError
        self._raise_failure()

    async def _shutdown(self) -> None:
        try:
            await asyncio.gather(*self._workers, return_exceptions=True)
        finally:
            try:
                self._supervisor_done.set()
                if self._supervisor is not None:
                    await self._supervisor
                if self._worker_count and self.operations_backend is not None:
                    await self._worker_heartbeat(stopped=True)
            finally:
                try:
                    if self._logs is not None:
                        await self._logs.__aexit__(None, None, None)
                finally:
                    try:
                        await self._backend.close()
                    finally:
                        if self._executor is not None:
                            # Owned callback futures have finished before this point.
                            self._executor.shutdown(wait=True)
                            self._executor = None
                        self._closed = True
                        self._stopped.set()

    def _check_loop(self) -> None:
        if self._loop is None or self._loop is not asyncio.get_running_loop():
            raise RuntimeError("Use the asyncio loop that started the dispatcher")
        if self._closed:
            raise RuntimeError("The dispatcher is closed")

    def _raise_failure(self) -> None:
        if self._failure is not None:
            raise self._failure

    def _fail(self, error: BaseException) -> None:
        self._accepting = False
        self._stop.set()
        if self._failure is None:
            self._failure = error
            self._failure_ready.set()
        _LOGGER.error("Pipeline execution stopped with %s", _error_name(error))

    async def _pause(self, duration: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=duration)
        except TimeoutError:
            pass

    async def _worker_heartbeat(self, *, stopped: bool = False) -> None:
        operations = self.operations_backend
        assert operations is not None
        await operations.worker_heartbeat(
            WorkerInfo(
                self._worker_id,
                state="stopped" if stopped else "draining" if self._stop.is_set() else "running",
                execution_mode=self._execution_mode,
                active_run_ids=tuple(self._active),
                started_at=self._started_at,
                hostname=socket.gethostname(),
                pid=os.getpid(),
            )
        )

    def _control(self, attempt: _Attempt, command: RunControl) -> None:
        if attempt.command == "force_stop" or attempt.command == command:
            return
        attempt.command = command
        if attempt.process is not None:
            attempt.process.cancel(force=command == "force_stop")
        elif attempt.task is not None and command == "cancel":
            attempt.task.cancel()
        # A thread cannot be killed safely. Keep unsupported force requests
        # pending, never falsely report a thread or receiver was terminated.

    async def _supervise(self) -> None:
        operations = self.operations_backend
        assert operations is not None
        try:
            while not self._supervisor_done.is_set():
                command = await operations.get_worker_command(self._worker_id)
                if command is not None:
                    self._stop.set()
                for attempt in tuple(self._active.values()):
                    requested = await operations.control(attempt.claim)
                    if command == "force_stop" and self._execution_mode == "process":
                        # Persist per-run requests first so a supervisor crash
                        # cannot revive operator-stopped work through recovery.
                        await operations.cancel(
                            attempt.claim.job.id,
                            force=True,
                            expected_token=attempt.claim.token,
                        )
                        requested = "force_stop"
                    if requested is not None:
                        self._control(attempt, requested)
                await self._worker_heartbeat()
                if self._stop.is_set() and all(worker.done() for worker in self._workers):
                    await self._worker_heartbeat(stopped=True)
                    self._stopped.set()
                    return
                try:
                    await asyncio.wait_for(
                        self._supervisor_done.wait(), timeout=min(self._poll_interval, 1)
                    )
                except TimeoutError:
                    pass
        except BaseException as error:
            self._fail(error)
            for attempt in tuple(self._active.values()):
                if attempt.process is not None:
                    attempt.process.cancel(force=True)
            self._stopped.set()

    async def _worker(self, owner: str) -> None:
        try:
            while not self._stop.is_set():
                claim = await self._backend.claim(owner)
                if claim is None:
                    await self._pause(self._poll_interval)
                else:
                    await self._execute(claim)
        except BaseException as error:
            self._fail(error)

    async def _execute(self, claim: Claim) -> None:
        finished = asyncio.Event()
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(claim, finished, lost))
        outcome: Outcome = "successful"
        error_code: str | None = None
        retry = False
        attempt = _Attempt(claim)
        self._active[claim.job.id] = attempt
        try:
            hook = self._by_key.get(claim.job.hook_id)
            if hook is None or self._registered.get(claim.job.hook_id) != claim.job.fingerprint:
                raise QueueMismatch("Claimed job does not match the running registry")
            try:
                event = self._codecs.decode(claim.job.event)
            except (ValueError, TypeError) as error:
                raise QueueMismatch("Claimed event cannot be decoded") from error
            with log_scope(run_id=claim.job.id, attempt=claim.attempt, worker_id=self._worker_id):
                emit_log(f"Pipeline hook {claim.job.hook_id} started", source="runtime")
                if self._execution_mode == "process":
                    assert hook.pipeline is not None
                    attempt.process = IsolatedRun(
                        hook.pipeline,
                        event,
                        self._harness,
                        worker_id=self._worker_id,
                        run_id=claim.job.id,
                        attempt=claim.attempt,
                        secrets=self._redaction_secrets,
                        emit=self._logs.accept if self._logs is not None else None,
                    )
                    # A command can arrive while event decoding completes.
                    if attempt.command is not None:
                        attempt.process.cancel(force=attempt.command == "force_stop")
                    result: ProcessResult = await attempt.process.run()
                    outcome, error_code, retry = result.outcome, result.error, result.retry
                else:
                    attempt.task = asyncio.create_task(self._invoke_captured(hook, event))
                    if attempt.command == "cancel":
                        attempt.task.cancel()
                    invocation_error = await attempt.task
                    if invocation_error is not None:
                        raise invocation_error
        except PromptFailed as error:
            code = error.result.failure.code if error.result.failure is not None else "PromptFailed"
            uncertain = code in {"anyo.outcome_unknown", "anyo.timeout"}
            outcome = "interrupted" if uncertain else "failed"
            error_code = code if uncertain else "PromptFailed"
            retry = not uncertain
        except QueueMismatch as error:
            outcome, error_code = "interrupted", _error_name(error)
            self._fail(error)
        except Exception as error:
            outcome, error_code, retry = "failed", _error_name(error), True
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError) and attempt.command is not None:
                outcome, error_code = "cancelled", "anyo.cancelled"
            else:
                outcome, error_code = "interrupted", _error_name(error)
                self._fail(error)
        finally:
            finished.set()
            # Heartbeat owns an in-flight storage operation until it exits.
            await heartbeat
        try:
            # Completion is the acknowledgement. A pending request observed
            # after callback completion still prohibits automatic replay.
            operations = self.operations_backend
            if operations is not None and not lost.is_set():
                command = await operations.control(claim)
                if command is not None:
                    outcome, error_code, retry = "cancelled", "anyo.cancelled", False
            if attempt.command is not None:
                outcome, retry = "cancelled", False
                error_code = (
                    "anyo.force_stopped"
                    if attempt.command == "force_stop" and self._execution_mode == "process"
                    else "anyo.cancelled"
                )
            elif self._execution_mode == "process" and self._failure is not None:
                # Termination after supervisor/storage failure is not an
                # acknowledged operator cancellation and is never replayed.
                outcome, error_code, retry = "interrupted", _error_name(self._failure), False
            with log_scope(run_id=claim.job.id, attempt=claim.attempt, worker_id=self._worker_id):
                if error_code is not None:
                    _LOGGER.error("Pipeline hook %s ended with %s", claim.job.hook_id, error_code)
                else:
                    emit_log(f"Pipeline hook {claim.job.hook_id} completed", source="runtime")
            if not lost.is_set():
                committed = await self._backend.finish(
                    claim, outcome, error=error_code, retry=retry
                )
                if not committed:
                    self._fail(QueueUnavailable("Worker lease was lost before completion"))
        finally:
            self._active.pop(claim.job.id, None)

    async def _heartbeat(self, claim: Claim, finished: asyncio.Event, lost: asyncio.Event) -> None:
        try:
            while not finished.is_set():
                try:
                    await asyncio.wait_for(finished.wait(), timeout=self._lease_seconds / 3)
                    return
                except TimeoutError:
                    pass
                if not await self._backend.renew(claim):
                    lost.set()
                    self._fail(QueueUnavailable("Worker lease was lost"))
                    attempt = self._active.get(claim.job.id)
                    if attempt is not None and attempt.process is not None:
                        attempt.process.cancel(force=True)
                    return
        except BaseException as error:
            lost.set()
            self._fail(error)
            attempt = self._active.get(claim.job.id)
            if attempt is not None and attempt.process is not None:
                attempt.process.cancel(force=True)

    async def _invoke_captured(self, hook: Hook, event: HookEvent) -> BaseException | None:
        # SystemExit/KeyboardInterrupt escaping a Task would abort the entire
        # event loop before the worker can record and clean up the attempt.
        try:
            await self._invoke(hook, event)
        except BaseException as error:
            return error
        return None

    async def _invoke(self, hook: Hook, event: HookEvent) -> None:
        if hook.pipeline is None:
            raise QueueMismatch("Claimed hook has no pipeline")
        result: object
        if inspect.iscoroutinefunction(hook.pipeline):
            result = hook.pipeline(event)
        else:
            # This is to_thread's executor/context mechanism without another
            # Task: global task cancellation must not discard ownership of a
            # running synchronous function. The shielded Future stays awaitable.
            context = copy_context()
            assert self._executor is not None
            thread = asyncio.get_running_loop().run_in_executor(
                self._executor, context.run, hook.pipeline, event
            )
            cancelled = False
            while not thread.done():
                try:
                    await asyncio.shield(thread)
                except asyncio.CancelledError:
                    cancelled = True
                except BaseException:
                    # Retrieve it below, inside the worker's failure boundary.
                    break
            if cancelled:
                try:
                    result = thread.result()
                except BaseException:
                    result = None
                # A callable can create a coroutine in its worker thread. It has
                # not started yet, so close it instead of leaving it unawaited.
                if inspect.iscoroutine(result):
                    result.close()
                elif isinstance(result, asyncio.Future):
                    result.cancel()
                raise asyncio.CancelledError
            result = thread.result()
        if inspect.isawaitable(result):
            await result
