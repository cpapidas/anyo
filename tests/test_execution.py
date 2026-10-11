"""Behavioral checks for in-process pipeline execution."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any

import pytest

from anyo.execution import CapacityError, Dispatcher
from anyo.hooks import Hook, HookEvent, Pipeline


def run(coroutine: Coroutine[Any, Any, None]) -> None:
    async def bounded() -> None:
        await asyncio.wait_for(coroutine, timeout=5)

    asyncio.run(bounded())


def hook(identifier: str, pipeline: Pipeline) -> Hook:
    return Hook(identifier, "github", "pr_created", {}, pipeline)


def event(identifier: str = "delivery") -> HookEvent:
    return HookEvent(
        provider="github",
        action="pr_created",
        delivery_id=identifier,
        organization="owner",
        repository="repo",
        payload={"nested": {"value": "original"}},
        pull_request_number=5,
        head_sha="commit",
    )


def test_expiry_during_payload_copy_rejects_all_jobs_without_recording_delivery(monkeypatch):
    from copy import deepcopy

    async def scenario():
        calls = []
        dispatcher = Dispatcher()
        await dispatcher.start()
        registered = hook("deadline", calls.append)
        jobs = [(registered, event("one")), (registered, event("two"))]
        now = [0.0]

        def copy_and_advance(value):
            copied = deepcopy(value)
            now[0] += 2
            return copied

        with monkeypatch.context() as patch:
            patch.setattr(asyncio.get_running_loop(), "time", lambda: now[0])
            patch.setattr("anyo.execution._memory.deepcopy", copy_and_advance)
            with pytest.raises(TimeoutError, match="deadline"):
                dispatcher.submit(jobs, deadline=3)
        assert calls == []
        assert dispatcher.submit(jobs) == 2
        await dispatcher.close()
        assert {value.delivery_id for value in calls} == {"one", "two"}

    run(scenario())


@pytest.mark.parametrize("name", ["workers", "capacity", "max_records"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2"])
def test_invalid_integer_settings(name: str, value: object) -> None:
    with pytest.raises(ValueError, match=name):
        Dispatcher(**{name: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan"), "2"])
def test_invalid_retention(value: object) -> None:
    with pytest.raises(ValueError, match="retention_seconds"):
        Dispatcher(retention_seconds=value)  # type: ignore[arg-type]


def test_lifecycle_requires_start_and_rejects_reuse() -> None:
    async def scenario() -> None:
        dispatcher = Dispatcher()
        with pytest.raises(CapacityError):
            dispatcher.submit([])
        await dispatcher.start()
        with pytest.raises(RuntimeError, match="once"):
            await dispatcher.start()
        await dispatcher.close()
        await dispatcher.close()
        with pytest.raises(CapacityError):
            dispatcher.submit([])
        with pytest.raises(RuntimeError, match="once"):
            await dispatcher.start()

        unused = Dispatcher()
        await unused.close()
        with pytest.raises(RuntimeError, match="once"):
            await unused.start()

    run(scenario())


def test_sync_async_and_returned_awaitable_run_on_expected_threads() -> None:
    async def scenario() -> None:
        loop_thread = threading.get_ident()
        seen: list[tuple[str, int]] = []

        def synchronous(received: HookEvent) -> str:
            assert received.pull_request_number == 5
            seen.append(("sync", threading.get_ident()))
            return "discarded"

        async def asynchronous(received: HookEvent) -> str:
            assert received.head_sha == "commit"
            seen.append(("async", threading.get_ident()))
            return "discarded"

        def awaitable_factory(received: HookEvent) -> object:
            seen.append(("factory", threading.get_ident()))

            async def result() -> None:
                seen.append(("awaited", threading.get_ident()))

            return result()

        dispatcher = Dispatcher(workers=1)
        await dispatcher.start()
        try:
            assert (
                dispatcher.submit(
                    [
                        (hook("sync", synchronous), event()),
                        (hook("async", asynchronous), event()),
                        (hook("factory", awaitable_factory), event()),
                    ]
                )
                == 3
            )
            await dispatcher.wait_idle()
            assert [name for name, _ in seen] == ["sync", "async", "factory", "awaited"]
            assert seen[0][1] != loop_thread
            assert seen[1][1] == loop_thread
            assert seen[2][1] != loop_thread
            assert seen[3][1] == loop_thread
        finally:
            await dispatcher.close()

    run(scenario())


def test_concurrency_fifo_and_atomic_capacity_rejection() -> None:
    async def scenario() -> None:
        started = [asyncio.Event() for _ in range(5)]
        release = [asyncio.Event() for _ in range(5)]
        order: list[int] = []

        async def pipeline(received: HookEvent) -> None:
            index = int(received.delivery_id)
            order.append(index)
            started[index].set()
            await release[index].wait()

        registered = hook("hook", pipeline)
        dispatcher = Dispatcher(workers=2, capacity=3)
        await dispatcher.start()
        try:
            assert dispatcher.submit([(registered, event(str(i))) for i in range(3)]) == 3
            await started[0].wait()
            await started[1].wait()
            assert not started[2].is_set()
            with pytest.raises(CapacityError):
                dispatcher.submit([(registered, event("3")), (registered, event("4"))])
            release[0].set()
            await started[2].wait()
            assert order == [0, 1, 2]
            # One newly free slot cannot admit a two-job fan-out.
            with pytest.raises(CapacityError):
                dispatcher.submit([(registered, event("3")), (registered, event("4"))])
            release[1].set()
            release[2].set()
            await dispatcher.wait_idle()
            release[3].set()
            release[4].set()
            assert dispatcher.submit([(registered, event("3")), (registered, event("4"))]) == 2
            await dispatcher.wait_idle()
            assert order == [0, 1, 2, 3, 4]
        finally:
            for gate in release:
                gate.set()
            await dispatcher.close()

    run(scenario())


def test_active_and_successful_duplicates_are_skipped() -> None:
    async def scenario() -> None:
        release = asyncio.Event()
        started = asyncio.Event()
        calls: list[str] = []

        async def pipeline(received: HookEvent) -> None:
            calls.append(received.delivery_id)
            started.set()
            await release.wait()

        registered = hook("hook", pipeline)
        delivery = (registered, event())
        dispatcher = Dispatcher(workers=1, capacity=1)
        await dispatcher.start()
        try:
            assert dispatcher.submit([delivery, delivery]) == 1
            assert dispatcher.submit([delivery]) == 0
            await started.wait()
            assert dispatcher.submit([delivery]) == 0
            release.set()
            await dispatcher.wait_idle()
            assert dispatcher.submit([delivery]) == 0
            assert calls == ["delivery"]
        finally:
            release.set()
            await dispatcher.close()

    run(scenario())


def test_failed_hook_retries_without_rerunning_successful_fanout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        attempts: list[str] = []

        async def successful(received: HookEvent) -> None:
            attempts.append("successful")

        async def flaky(received: HookEvent) -> None:
            attempts.append("flaky")
            if attempts.count("flaky") == 1:
                raise ValueError("do not log this secret or payload")

        jobs = [(hook("good", successful), event()), (hook("bad", flaky), event())]
        dispatcher = Dispatcher(workers=1, max_records=2)
        await dispatcher.start()
        try:
            assert dispatcher.submit(jobs) == 2
            await dispatcher.wait_idle()
            assert attempts == ["successful", "flaky"]
            assert dispatcher.submit(jobs) == 1
            await dispatcher.wait_idle()
            assert attempts == ["successful", "flaky", "flaky"]
            assert dispatcher.submit(jobs) == 0
        finally:
            await dispatcher.close()

    run(scenario())
    assert "ValueError" in caplog.text
    assert "do not log this secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_payload_is_copied_per_hook_and_before_caller_mutation() -> None:
    async def scenario() -> None:
        received_values: list[object] = []

        async def pipeline(received: HookEvent) -> None:
            nested = received.payload["nested"]
            assert isinstance(nested, dict)
            received_values.append(nested["value"])
            nested["value"] = "callback mutation"

        original = event()
        dispatcher = Dispatcher(workers=1)
        await dispatcher.start()
        try:
            assert (
                dispatcher.submit(
                    [(hook("first", pipeline), original), (hook("second", pipeline), original)]
                )
                == 2
            )
            nested = original.payload["nested"]
            assert isinstance(nested, dict)
            nested["value"] = "caller mutation"
            await dispatcher.wait_idle()
            assert received_values == ["original", "original"]
            assert nested["value"] == "caller mutation"
        finally:
            await dispatcher.close()

    run(scenario())


def test_payload_copy_failure_does_not_partially_admit_jobs() -> None:
    class Uncopyable:
        def __deepcopy__(self, memo: dict[int, object]) -> object:
            raise ValueError("Cannot copy this payload")

    async def scenario() -> None:
        calls: list[str] = []

        async def pipeline(received: HookEvent) -> None:
            calls.append(received.delivery_id)

        registered = hook("hook", pipeline)
        broken = HookEvent("github", "pr_created", "broken", "owner", "repo", {"x": Uncopyable()})
        dispatcher = Dispatcher(max_records=2)
        await dispatcher.start()
        try:
            with pytest.raises(ValueError, match="Cannot copy"):
                dispatcher.submit([(registered, event("first")), (registered, broken)])
            await dispatcher.wait_idle()
            assert calls == []
            assert (
                dispatcher.submit([(registered, event("first")), (registered, event("broken"))])
                == 2
            )
            await dispatcher.wait_idle()
            assert calls == ["first", "broken"]
        finally:
            await dispatcher.close()

    run(scenario())


def test_history_retention_starts_at_completion_and_never_evicts_active_jobs() -> None:
    async def scenario() -> None:
        now = [0.0]
        started = asyncio.Event()
        release = asyncio.Event()
        attempts: list[str] = []

        async def pipeline(received: HookEvent) -> None:
            attempts.append(received.delivery_id)
            started.set()
            await release.wait()

        registered = hook("hook", pipeline)
        dispatcher = Dispatcher(
            workers=1, capacity=2, max_records=1, retention_seconds=10, clock=lambda: now[0]
        )
        await dispatcher.start()
        try:
            assert dispatcher.submit([(registered, event("first"))]) == 1
            await started.wait()
            now[0] = 100.0
            assert dispatcher.submit([(registered, event("first"))]) == 0
            with pytest.raises(CapacityError, match="history"):
                dispatcher.submit([(registered, event("second"))])
            release.set()
            await dispatcher.wait_idle()
            now[0] = 109.0
            with pytest.raises(CapacityError, match="history"):
                dispatcher.submit([(registered, event("second"))])
            assert dispatcher.submit([(registered, event("first"))]) == 0
            now[0] = 110.0
            assert dispatcher.submit([(registered, event("first"))]) == 1
            await dispatcher.wait_idle()
            assert attempts == ["first", "first"]
        finally:
            release.set()
            await dispatcher.close()

    run(scenario())


def test_history_rejection_does_not_partially_admit_fanout() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def pipeline(received: HookEvent) -> None:
            calls.append(received.delivery_id)

        registered = hook("hook", pipeline)
        dispatcher = Dispatcher(max_records=2)
        await dispatcher.start()
        try:
            assert dispatcher.submit([(registered, event("first"))]) == 1
            await dispatcher.wait_idle()
            with pytest.raises(CapacityError, match="history"):
                dispatcher.submit([(registered, event("second")), (registered, event("third"))])
            await dispatcher.wait_idle()
            assert calls == ["first"]
            assert dispatcher.submit([(registered, event("second"))]) == 1
            await dispatcher.wait_idle()
            assert calls == ["first", "second"]
        finally:
            await dispatcher.close()

    run(scenario())


def test_close_drains_running_and_queued_work_and_stops_admission() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        closing = asyncio.Event()
        completed: list[str] = []

        async def pipeline(received: HookEvent) -> None:
            started.set()
            await release.wait()
            completed.append(received.delivery_id)

        dispatcher = Dispatcher(workers=1)
        registered = hook("hook", pipeline)
        await dispatcher.start()
        dispatcher.submit([(registered, event("first")), (registered, event("second"))])
        await started.wait()

        async def shutdown() -> None:
            closing.set()
            await dispatcher.close()

        shutdown_task = asyncio.create_task(shutdown())
        await closing.wait()
        assert not shutdown_task.done()
        with pytest.raises(CapacityError):
            dispatcher.submit([(registered, event("third"))])
        release.set()
        await shutdown_task
        assert completed == ["first", "second"]

    run(scenario())


def test_cancelling_close_waits_for_owned_work_then_propagates_cancellation() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        closing = asyncio.Event()
        completed = asyncio.Event()

        async def pipeline(received: HookEvent) -> None:
            started.set()
            await release.wait()
            completed.set()

        dispatcher = Dispatcher(workers=1)
        await dispatcher.start()
        dispatcher.submit([(hook("hook", pipeline), event())])
        await started.wait()

        async def shutdown() -> None:
            closing.set()
            await dispatcher.close()

        shutdown_task = asyncio.create_task(shutdown())
        await closing.wait()
        shutdown_task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown_task
        assert completed.is_set()
        await dispatcher.close()

    run(scenario())


@pytest.mark.parametrize("failure", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
def test_process_control_failure_is_propagated_without_stranding_queue(
    failure: type[BaseException],
) -> None:
    async def scenario() -> None:
        seen: list[str] = []

        async def pipeline(received: HookEvent) -> None:
            seen.append(received.delivery_id)
            raise failure()

        dispatcher = Dispatcher(workers=1)
        registered = hook("hook", pipeline)
        await dispatcher.start()
        dispatcher.submit([(registered, event("first")), (registered, event("second"))])
        with pytest.raises(failure):
            await dispatcher.wait_idle()
        with pytest.raises(CapacityError):
            dispatcher.submit([(registered, event("third"))])
        with pytest.raises(failure):
            await dispatcher.close()
        assert seen == ["first"]

    run(scenario())


def test_fatal_failure_preserves_ownership_of_other_running_work() -> None:
    async def scenario() -> None:
        running = asyncio.Event()
        failed = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def pipeline(received: HookEvent) -> None:
            if received.delivery_id == "running":
                running.set()
                await release.wait()
                finished.set()
            else:
                await running.wait()
                failed.set()
                raise asyncio.CancelledError()

        dispatcher = Dispatcher(workers=2)
        registered = hook("hook", pipeline)
        await dispatcher.start()
        dispatcher.submit([(registered, event("running")), (registered, event("cancelled"))])
        await failed.wait()
        shutdown_task = asyncio.create_task(dispatcher.close())
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown_task
        assert finished.is_set()

    run(scenario())
