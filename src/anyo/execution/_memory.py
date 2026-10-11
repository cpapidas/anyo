"""Bounded, in-process execution for registered webhook pipelines."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import time
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Literal

from anyo.execution._contracts import CapacityError
from anyo.hooks import Hook, HookEvent, HookResponse, WebhookResponse

__all__ = ["CapacityError", "Dispatcher", "ResponseDispatcher"]

_LOGGER = logging.getLogger(__name__)
_Key = tuple[str, str]


@dataclass
class _Record:
    status: Literal["active", "successful", "failed"] = "active"
    completed_at: float | None = None


@dataclass(frozen=True)
class _Job:
    key: _Key
    hook: Hook
    event: HookEvent


class Dispatcher:
    """Execute hooks with bounded concurrency and process-local deduplication.

    All methods belong to the asyncio loop that calls ``start``. A dispatcher
    is single-use. Normal shutdown drains accepted work without a timeout;
    consumers must bound their callbacks' external operations themselves.

    Successful and active deliveries suppress redelivery. Ordinary failures
    allow explicit redelivery. Cancellation or process-control exceptions stop
    admission, fail queued work, and propagate from ``wait_idle`` or ``close``
    after other running callbacks finish.
    """

    def __init__(
        self,
        *,
        workers: int = 4,
        capacity: int = 100,
        retention_seconds: float = 3600,
        max_records: int = 10000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, value in (
            ("workers", workers),
            ("capacity", capacity),
            ("max_records", max_records),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(retention_seconds, bool)
            or not isinstance(retention_seconds, (int, float))
            or not math.isfinite(retention_seconds)
            or retention_seconds <= 0
        ):
            raise ValueError("retention_seconds must be finite and positive")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._worker_count = workers
        self._capacity = capacity
        self._retention_seconds = retention_seconds
        self._max_records = max_records
        self._clock = clock
        self._queue: asyncio.Queue[_Job | None] = asyncio.Queue()
        self._records: dict[_Key, _Record] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._idle = asyncio.Event()
        self._idle.set()
        self._active_count = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._accepting = False
        self._closed = False
        self._shutdown_task: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None

    async def start(self) -> None:
        """Start workers on the current loop; reject reuse of this dispatcher."""
        if self._loop is not None or self._closed:
            raise RuntimeError("A dispatcher can only be started once")
        self._loop = asyncio.get_running_loop()
        self._workers = [
            asyncio.create_task(self._worker(), name=f"anyo-pipeline-{index}")
            for index in range(self._worker_count)
        ]
        self._accepting = True

    def submit(
        self, jobs: Sequence[tuple[Hook, HookEvent]], *, deadline: float | None = None
    ) -> int:
        """Atomically admit newly eligible jobs and return their count.

        Raise ``CapacityError`` when not accepting work, when running plus
        queued work would exceed capacity, or when retained history is full.
        Payload copies are created before admission; caller mutation after
        submission cannot change accepted inputs.
        An optional event-loop deadline rejects late admission with TimeoutError,
        including time spent copying payloads, without admitting any jobs.
        """
        if not self._accepting:
            raise CapacityError("The dispatcher is not accepting deliveries")
        self._check_loop()
        now = self._clock()
        self._records = {
            key: record
            for key, record in self._records.items()
            if record.completed_at is None or now - record.completed_at < self._retention_seconds
        }

        eligible: dict[_Key, tuple[Hook, HookEvent]] = {}
        for hook, event in jobs:
            if hook.pipeline is None:
                raise ValueError("A background job requires a pipeline")
            key = (hook.id, event.delivery_id)
            record = self._records.get(key)
            if record is None or record.status == "failed":
                eligible.setdefault(key, (hook, event))

        if self._active_count + len(eligible) > self._capacity:
            raise CapacityError("Pipeline capacity is full")
        new_records = sum(key not in self._records for key in eligible)
        if len(self._records) + new_records > self._max_records:
            raise CapacityError("Delivery history capacity is full")

        copied_jobs = [
            _Job(key, hook, replace(event, payload=deepcopy(dict(event.payload))))
            for key, (hook, event) in eligible.items()
        ]
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Delivery admission deadline exceeded")
        for job in copied_jobs:
            self._records[job.key] = _Record()
            self._queue.put_nowait(job)
        self._active_count += len(copied_jobs)
        if copied_jobs:
            self._idle.clear()
        return len(copied_jobs)

    async def wait_idle(self) -> None:
        """Wait for accepted work and propagate any fatal worker failure."""
        self._check_loop()
        await self._idle.wait()
        if self._failure is not None:
            raise self._failure

    async def close(self) -> None:
        """Stop admission and drain work, preserving caller cancellation.

        Repeated calls are safe. Closing an unused dispatcher permanently
        closes it. The first cancellation while waiting still allows cleanup
        to finish before cancellation is re-raised.
        """
        if self._loop is None:
            self._closed = True
            return
        self._check_loop()
        self._accepting = False
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        try:
            await asyncio.shield(self._shutdown_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._shutdown_task)
            raise
        if self._failure is not None:
            raise self._failure

    def _check_loop(self) -> None:
        if self._loop is None or asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Use the asyncio loop that started the dispatcher")

    async def _shutdown(self) -> None:
        await self._idle.wait()
        for worker in self._workers:
            if not worker.done():
                self._queue.put_nowait(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._closed = True

    async def _worker(self) -> None:
        try:
            while True:
                job = await self._queue.get()
                if job is None:
                    self._queue.task_done()
                    return
                successful = False
                try:
                    result: object
                    assert job.hook.pipeline is not None
                    if inspect.iscoroutinefunction(job.hook.pipeline):
                        result = job.hook.pipeline(job.event)
                    else:
                        result = await asyncio.to_thread(job.hook.pipeline, job.event)
                    if inspect.isawaitable(result):
                        await result
                    successful = True
                except Exception as error:
                    _LOGGER.error(
                        "Pipeline hook %s failed with %s", job.hook.id, type(error).__name__
                    )
                finally:
                    self._complete(job, successful=successful)
        except BaseException as error:
            self._accepting = False
            if self._failure is None:
                self._failure = error
            _LOGGER.error("Pipeline execution stopped with %s", type(error).__name__)
            # A terminated worker must never strand accepted jobs in the queue.
            # Other callbacks already running remain owned until they finish.
            while not self._queue.empty():
                pending = self._queue.get_nowait()
                if pending is None:
                    self._queue.task_done()
                    break
                else:
                    self._complete(pending, successful=False)

    def _complete(self, job: _Job, *, successful: bool) -> None:
        self._records[job.key] = _Record(
            status="successful" if successful else "failed",
            completed_at=self._clock(),
        )
        self._active_count -= 1
        self._queue.task_done()
        if not self._active_count:
            self._idle.set()


@dataclass
class _ResponseRecord:
    task: asyncio.Task[WebhookResponse]
    completed_at: float | None = None


class ResponseDispatcher:
    """Bounded immediate async work, separate from background pipeline workers.

    Successful replies are replayed on duplicates. Cancellation never releases
    an active slot until the consumer coroutine actually exits. Consumers must
    respect cancellation and avoid blocking the event loop.
    """

    def __init__(
        self,
        *,
        workers: int,
        max_records: int,
        retention_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Reuse the execution settings validation without starting any workers.
        Dispatcher(workers=workers, max_records=max_records, retention_seconds=retention_seconds)
        self._workers = workers
        self._max_records = max_records
        self._retention_seconds = retention_seconds
        self._clock = clock
        self._records: dict[_Key, _ResponseRecord] = {}
        self._closed = False

    async def respond(
        self,
        hook: Hook,
        event: HookEvent,
        render: Callable[[HookResponse], WebhookResponse],
        *,
        deadline: float | None = None,
    ) -> WebhookResponse:
        if self._closed:
            raise CapacityError("Responder is shutting down")
        now = self._clock()
        self._records = {
            key: record
            for key, record in self._records.items()
            if record.completed_at is None or now - record.completed_at < self._retention_seconds
        }
        key = (hook.id, event.delivery_id)
        record = self._records.get(key)
        if record is None:
            active = sum(not item.task.done() for item in self._records.values())
            if active >= self._workers or len(self._records) >= self._max_records:
                raise CapacityError("Immediate response capacity unavailable")
            private_event = replace(event, payload=deepcopy(dict(event.payload)))
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Immediate response admission deadline exceeded")

            async def run() -> WebhookResponse:
                assert hook.responder is not None
                result = await hook.responder(private_event)
                if not isinstance(result, HookResponse) or type(result.dispatch) is not bool:
                    raise ValueError("Responder must return HookResponse")
                return render(result)

            task = asyncio.create_task(run(), name=f"anyo-response-{hook.id}")
            record = _ResponseRecord(task)
            self._records[key] = record

            def complete(done: asyncio.Task[WebhookResponse]) -> None:
                if done.cancelled() or done.exception() is not None:
                    self._records.pop(key, None)
                else:
                    record.completed_at = self._clock()

            task.add_done_callback(complete)
        try:
            return await asyncio.shield(record.task)
        except asyncio.CancelledError:
            record.task.cancel()
            raise

    async def close(self) -> None:
        """Cancel active responders and retain ownership until they finish."""
        self._closed = True
        tasks = [record.task for record in self._records.values() if not record.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            cleanup = asyncio.gather(*tasks, return_exceptions=True)
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await asyncio.shield(cleanup)
                raise
        self._records.clear()
