"""Worker behavior checked at a controlled storage boundary."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import replace

import pytest

from anyo.agents import PromptFailure, PromptResult, runtime_failure
from anyo.execution._codec import EventCodecRegistry
from anyo.execution._contracts import Claim, JobInfo, QueueMismatch, QueueUnavailable
from anyo.execution._worker import DurableDispatcher
from anyo.hooks import Hook, HookEvent, RetryPolicy


class QueueStub:
    """Controllable persistence boundary; backend atomicity is tested elsewhere."""

    def __init__(self):
        self.pending = deque()
        self.admitted = []
        self.finishes = []
        self.records = {}
        self.opened = None
        self.closed = False
        self.admit_started = asyncio.Event()
        self.allow_admit = asyncio.Event()
        self.allow_admit.set()
        self.renewed = asyncio.Event()
        self.finished = asyncio.Event()
        self.renew_result = True
        self.claim_error = None
        self.renew_error = None
        self.claims = 0
        self.retry_ids = []

    async def open(self, registry):
        self.opened = dict(registry)

    async def admit(self, jobs):
        self.admit_started.set()
        await self.allow_admit.wait()
        self.admitted.extend(jobs)
        self.pending.extend(jobs)
        for job in jobs:
            self.records[job.id] = JobInfo(job.id, job.hook_id, "queued", 0, 0)
        return len(jobs)

    async def claim(self, owner):
        if self.claim_error is not None:
            raise self.claim_error
        if not self.pending:
            return None
        self.claims += 1
        job = self.pending.popleft()
        self.records[job.id] = JobInfo(job.id, job.hook_id, "running", 1, 0)
        return Claim(job, f"token-{self.claims}", 1)

    async def renew(self, claim):
        self.renewed.set()
        if self.renew_error is not None:
            raise self.renew_error
        return self.renew_result

    async def finish(self, claim, status, *, error=None, retry=False):
        self.finishes.append((claim, status, error, retry))
        self.records[claim.job.id] = JobInfo(
            claim.job.id, claim.job.hook_id, status, claim.attempt, 0, error=error
        )
        self.finished.set()
        return True

    async def inspect(self, *, limit=100):
        return list(self.records.values())[:limit]

    async def retry(self, job_id):
        self.retry_ids.append(job_id)
        return job_id in self.records

    async def close(self):
        self.closed = True


def hook(key, callback, **kwargs):
    return Hook("registration-local", "test", "ready", {}, callback, stable_key=key, **kwargs)


def event(identifier="delivery"):
    return HookEvent("test", "ready", identifier, payload={"nested": {"value": "original"}})


def run(scenario):
    async def bounded():
        await asyncio.wait_for(scenario(), 5)

    asyncio.run(bounded())


def test_sync_pipelines_cannot_starve_storage_default_executor():
    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        started = asyncio.Event()
        release = threading.Event()

        def callback(event):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)

        queue = QueueStub()
        registered = hook("sync", callback)
        runner = DurableDispatcher(queue, [registered], EventCodecRegistry(), workers=1)
        await runner.start()
        try:
            await runner.submit([(registered, event())])
            await started.wait()
            # SQLite uses this executor too. A live callback must not occupy it.
            assert await asyncio.wait_for(asyncio.to_thread(lambda: "storage"), 1) == "storage"
        finally:
            release.set()
            await runner.close()

    run(scenario)


def test_receiver_waits_for_storage_and_serializes_without_executing_callbacks():
    async def scenario():
        backend = QueueStub()
        calls = []
        registered = hook("stable", calls.append)
        dispatcher = DurableDispatcher(
            backend, [registered], EventCodecRegistry(), run_workers=False
        )
        await dispatcher.start()
        backend.allow_admit.clear()
        original = event()
        admission = asyncio.create_task(dispatcher.submit([(registered, original)]))
        await backend.admit_started.wait()
        assert not admission.done()
        original.payload["nested"]["value"] = "changed"
        backend.allow_admit.set()
        assert await admission == 1
        assert not calls
        assert EventCodecRegistry().decode(backend.admitted[0].event).payload == {
            "nested": {"value": "original"}
        }
        await dispatcher.submit([(registered, event())])
        assert backend.admitted[0].id == backend.admitted[1].id
        assert await dispatcher.retry(backend.admitted[0].id)
        assert len(await dispatcher.inspect()) == 1
        await dispatcher.close()
        assert backend.closed and backend.claims == 0

    run(scenario)


def test_serialization_and_expired_deadline_never_partially_admit():
    async def scenario():
        backend = QueueStub()
        registered = hook("stable", lambda event: None)
        dispatcher = DurableDispatcher(
            backend, [registered], EventCodecRegistry(), run_workers=False
        )
        await dispatcher.start()
        with pytest.raises(ValueError, match="JSON"):
            await dispatcher.submit(
                [(registered, event()), (registered, replace(event(), payload={"bad": object()}))]
            )
        with pytest.raises(TimeoutError, match="deadline"):
            await dispatcher.submit(
                [(registered, event())], deadline=asyncio.get_running_loop().time()
            )
        assert not backend.admitted and not backend.admit_started.is_set()
        await dispatcher.close()

    run(scenario)


def test_sync_async_callable_and_returned_awaitable_execution():
    async def scenario():
        backend = QueueStub()
        seen = []
        loop_thread = threading.get_ident()
        context = ContextVar("worker-test-context", default="outside")
        token = context.set("worker-owned")

        def sync(received):
            assert context.get() == "worker-owned"
            seen.append(("sync", threading.get_ident(), received))
            return runtime_failure("ignored", "timeout", "ignored result")

        async def asynchronous(received):
            assert context.get() == "worker-owned"
            seen.append(("async", threading.get_ident(), received))

        def factory(received):
            seen.append(("factory", threading.get_ident(), received))
            return asynchronous(received)

        class Callback:
            async def __call__(self, received):
                seen.append(("object", threading.get_ident(), received))

        registrations = [
            hook("sync", sync),
            hook("async", asynchronous),
            hook("factory", factory),
            hook("object", Callback()),
        ]
        dispatcher = DurableDispatcher(
            backend, registrations, EventCodecRegistry(), workers=1, poll_interval=0.01
        )
        await dispatcher.start()
        context.reset(token)
        original = event()
        await dispatcher.submit([(registered, original) for registered in registrations])
        await dispatcher.wait_idle()
        assert [name for name, _, _ in seen] == ["sync", "async", "factory", "async", "object"]
        assert seen[0][1] != loop_thread and seen[2][1] != loop_thread
        assert seen[1][1] == seen[3][1] == seen[4][1] == loop_thread
        assert all(received is not original for _, _, received in seen)
        assert all(status == "successful" for _, status, _, _ in backend.finishes)
        await dispatcher.close()

    run(scenario)


def test_close_finishes_active_work_leaves_backlog_and_preserves_caller_cancellation():
    async def scenario():
        backend = QueueStub()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def callback(received):
            calls.append(received.delivery_id)
            entered.set()
            await release.wait()

        registered = hook("stable", callback)
        dispatcher = DurableDispatcher(backend, [registered], EventCodecRegistry(), workers=1)
        await dispatcher.start()
        await dispatcher.submit([(registered, event("one")), (registered, event("two"))])
        await entered.wait()
        closing = asyncio.create_task(dispatcher.close())
        # The task-start barrier ensures cancellation happens inside close.
        started = asyncio.Event()
        asyncio.get_running_loop().call_soon(started.set)
        await started.wait()
        closing.cancel()
        assert not backend.closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert calls == ["one"]
        assert backend.closed
        assert backend.records[backend.admitted[1].id].status == "queued"

    run(scenario)


@pytest.mark.parametrize("code", ["timeout", "outcome_unknown"])
def test_uncertain_prompt_failure_is_never_automatically_retried(code):
    async def scenario():
        backend = QueueStub()

        async def callback(received):
            runtime_failure("run", code, "uncertain result").raise_for_failure()

        registered = hook("stable", callback, retry=RetryPolicy(5, 0, True))
        dispatcher = DurableDispatcher(
            backend, [registered], EventCodecRegistry(), poll_interval=0.01
        )
        await dispatcher.start()
        await dispatcher.submit([(registered, event())])
        await dispatcher.wait_idle()
        _, status, error, retry = backend.finishes[0]
        assert (status, error, retry) == ("interrupted", f"anyo.{code}", False)
        await dispatcher.close()

    run(scenario)


def test_ordinary_failure_requests_bounded_backend_retry_without_exception_text(caplog):
    async def scenario():
        backend = QueueStub()

        def callback(received):
            raise ValueError("private payload and credentials")

        registered = hook("stable", callback, retry=RetryPolicy(3, 2))
        dispatcher = DurableDispatcher(
            backend, [registered], EventCodecRegistry(), poll_interval=0.01
        )
        await dispatcher.start()
        await dispatcher.submit([(registered, event())])
        await dispatcher.wait_idle()
        claim, status, error, retry = backend.finishes[0]
        assert (status, error, retry) == ("failed", "ValueError", True)
        assert claim.job.max_attempts == 3 and claim.job.retry_delay == 2
        assert "private payload" not in caplog.text
        await dispatcher.close()

    run(scenario)


@pytest.mark.parametrize("storage_error", [False, True])
def test_heartbeat_failure_stops_claims_and_skips_stale_completion(storage_error):
    async def scenario():
        backend = QueueStub()
        release, entered = asyncio.Event(), asyncio.Event()
        if storage_error:
            backend.renew_error = QueueUnavailable("Storage is unavailable")
        else:
            backend.renew_result = False

        async def callback(received):
            entered.set()
            await release.wait()

        registered = hook("stable", callback)
        dispatcher = DurableDispatcher(
            backend, [registered], EventCodecRegistry(), workers=1, lease_seconds=0.03
        )
        await dispatcher.start()
        await dispatcher.submit([(registered, event("one")), (registered, event("two"))])
        await entered.wait()
        await backend.renewed.wait()
        release.set()
        with pytest.raises(QueueUnavailable):
            await dispatcher.close()
        assert backend.claims == 1
        assert not backend.finishes
        assert backend.closed

    run(scenario)


def test_sync_process_control_error_is_owned_and_propagated_after_cleanup():
    async def scenario():
        backend = QueueStub()

        def callback(received):
            raise SystemExit("private detail")

        registered = hook("stable", callback)
        dispatcher = DurableDispatcher(backend, [registered], EventCodecRegistry(), workers=1)
        await dispatcher.start()
        await dispatcher.submit([(registered, event())])
        await backend.finished.wait()
        with pytest.raises(SystemExit):
            await dispatcher.close()
        assert backend.closed
        assert backend.finishes[0][1:] == ("interrupted", "SystemExit", False)

    run(scenario)


def test_worker_cancellation_does_not_abandon_a_running_sync_thread():
    async def scenario():
        backend = QueueStub()
        release = threading.Event()
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        def callback(received):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3)

        registered = hook("stable", callback)
        dispatcher = DurableDispatcher(backend, [registered], EventCodecRegistry(), workers=1)
        await dispatcher.start()
        await dispatcher.submit([(registered, event())])
        await entered.wait()
        dispatcher._workers[0].cancel()
        closing = asyncio.create_task(dispatcher.close())
        assert not backend.closed and not backend.finishes
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert backend.closed
        assert backend.finishes[0][1:] == ("interrupted", "CancelledError", False)

    run(scenario)


def test_missing_registry_hook_is_diagnosable_and_never_runs_another_callback():
    async def scenario():
        backend = QueueStub()
        called = []
        registered = hook("stable", called.append)
        dispatcher = DurableDispatcher(
            backend, [registered], EventCodecRegistry(), run_workers=False
        )
        await dispatcher.start()
        await dispatcher.submit([(registered, event())])
        backend.pending[0] = replace(backend.pending[0], hook_id="unknown")
        claim = await backend.claim("fixture")
        await dispatcher._execute(claim)
        with pytest.raises(QueueMismatch):
            await dispatcher.close()
        assert not called
        assert backend.finishes[0][1:] == ("interrupted", "QueueMismatch", False)

    run(scenario)


def test_responder_only_registrations_do_not_need_worker_identity():
    async def scenario():
        async def responder(received):
            raise AssertionError("receiver owns responses")

        registered = Hook("reply", "test", "ready", {}, None, responder=responder)
        backend = QueueStub()
        dispatcher = DurableDispatcher(backend, [registered], EventCodecRegistry())
        await dispatcher.start()
        assert backend.opened == {}
        await dispatcher.close()

    run(scenario)


def test_claim_failure_wakes_host_and_releases_storage():
    async def scenario():
        backend = QueueStub()
        backend.claim_error = QueueUnavailable("Storage is unavailable")
        registered = hook("stable", lambda received: None)
        dispatcher = DurableDispatcher(backend, [registered], EventCodecRegistry())
        await dispatcher.start()
        with pytest.raises(QueueUnavailable):
            await dispatcher.wait_failure()
        with pytest.raises(QueueUnavailable):
            await dispatcher.wait_idle()
        with pytest.raises(QueueUnavailable):
            await dispatcher.close()
        assert backend.closed

    run(scenario)


@pytest.mark.parametrize("source", ["agent-code", "exception-name"])
def test_untrusted_failure_codes_and_exception_names_do_not_enter_storage_or_logs(source, caplog):
    async def scenario():
        private = "private credential\n" + "x" * 200
        backend = QueueStub()

        async def callback(received):
            if source == "agent-code":
                result = PromptResult(
                    "run", failure=PromptFailure(None, private, "private message", "agent")
                )
                result.raise_for_failure()
            else:
                unsafe = type(private, (Exception,), {})
                raise unsafe("private message")

        registered = hook("stable", callback)
        dispatcher = DurableDispatcher(
            backend, [registered], EventCodecRegistry(), poll_interval=0.01
        )
        await dispatcher.start()
        await dispatcher.submit([(registered, event())])
        await dispatcher.wait_idle()
        expected = "PromptFailed" if source == "agent-code" else "PipelineError"
        assert backend.finishes[0][2] == expected
        assert "private" not in caplog.text
        await dispatcher.close()

    run(scenario)
