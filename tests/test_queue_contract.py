"""The same consumer/queue contract exercised against both concrete backends."""

import asyncio
import os
import uuid

import pytest

from anyo.execution import CapacityError, Job, QueueMismatch, QueueSettings
from anyo.execution._codec import EventCodecRegistry
from anyo.execution._worker import DurableDispatcher
from anyo.hooks import Hook, HookEvent, RetryPolicy
from anyo.integrations.redis import RedisQueue
from anyo.integrations.sqlite import SqliteQueue


@pytest.fixture(params=["sqlite", pytest.param("redis", marks=pytest.mark.live)])
def make_queue(request, tmp_path):
    namespace = "contract-" + uuid.uuid4().hex
    if request.param == "redis":
        url = os.environ.get("ANYO_TEST_REDIS_URL")
        if not url:
            pytest.skip("Set ANYO_TEST_REDIS_URL to select real Redis verification")
        factory = RedisQueue
    else:
        url, factory = f"sqlite:///{tmp_path / 'contract.db'}", SqliteQueue
    return lambda settings=QueueSettings(): factory(url, namespace, settings)


def run(coro):
    async def bounded():
        async with asyncio.timeout(10):
            await coro

    asyncio.run(bounded())


def test_atomic_fanout_limits_and_partial_redelivery(make_queue):
    async def scenario():
        queue = make_queue(QueueSettings(capacity=2))
        await queue.open({"hook": "v1"})
        jobs = [Job(str(n), "hook", "v1", "{}") for n in range(3)]
        try:
            with pytest.raises(CapacityError):
                await queue.admit(jobs)
            assert await queue.inspect() == []
            assert await queue.admit(jobs[:2]) == 2
            first, second = await queue.claim("one"), await queue.claim("two")
            assert first and second and first.job.id != second.job.id
            assert await queue.finish(first, "successful")
            assert await queue.finish(second, "failed", error="ValueError")
            assert await queue.admit(jobs[:2]) == 1
            replay = await queue.claim("three")
            assert replay.job.id == second.job.id and replay.attempt == 1
            assert await queue.finish(replay, "successful")
            assert await queue.admit(jobs[:2]) == 0
        finally:
            await queue.close()

    run(scenario())


def test_concurrent_consumers_and_persisted_registry(make_queue):
    async def scenario():
        first, second = make_queue(), make_queue()
        await first.open({"hook": "v1"})
        await second.open({"hook": "v1"})
        try:
            jobs = [Job(str(n), "hook", "v1", "{}") for n in range(8)]
            accepted = await asyncio.gather(first.admit(jobs), second.admit(jobs))
            assert sorted(accepted) == [0, 8]
            claims = await asyncio.gather(
                *(q.claim(str(n)) for n, q in enumerate([first, second] * 4))
            )
            assert len({claim.job.id for claim in claims if claim}) == 8
            for claim in claims:
                assert await first.finish(claim, "successful")
            changed = make_queue()
            try:
                with pytest.raises(QueueMismatch):
                    await changed.open({"hook": "v2"})
            finally:
                await changed.close()
        finally:
            await first.close()
            await second.close()

    run(scenario())


def test_same_worker_callbacks_and_payloads_on_either_backend(make_queue):
    async def scenario():
        calls = []

        def sync(event):
            event.payload["nested"][0] = "changed"
            calls.append(("sync", event.delivery_id))

        async def asynchronous(event):
            assert event.payload == {"nested": ["original"]}
            calls.append(("async", event.delivery_id))

        hooks = [
            Hook("a", "custom", "event", {}, sync, stable_key="a"),
            Hook("b", "custom", "event", {}, asynchronous, stable_key="b"),
        ]
        receiver = DurableDispatcher(make_queue(), hooks, EventCodecRegistry(), run_workers=False)
        await receiver.start()
        event = HookEvent("custom", "event", "one", payload={"nested": ["original"]})
        assert await receiver.submit([(hook, event) for hook in hooks]) == 2
        await receiver.close()
        assert calls == []
        worker = DurableDispatcher(make_queue(), list(reversed(hooks)), EventCodecRegistry())
        await worker.start()
        try:
            await worker.wait_idle()
            assert sorted(calls) == [("async", "one"), ("sync", "one")]
            assert event.payload == {"nested": ["original"]}
        finally:
            await worker.close()

    run(scenario())


def test_worker_retry_budget_and_first_admitted_payload(make_queue):
    async def scenario():
        calls = []

        def fail_once(event):
            calls.append(event.payload)
            if len(calls) == 1:
                raise ValueError

        hook = Hook(
            "h",
            "custom",
            "event",
            {},
            fail_once,
            stable_key="retry",
            retry=RetryPolicy(max_attempts=2, delay=0),
        )
        runner = DurableDispatcher(make_queue(), [hook], EventCodecRegistry())
        await runner.start()
        try:
            event = HookEvent("custom", "event", "one", payload={"retry_num": 0})
            assert await runner.submit([(hook, event)]) == 1
            await runner.wait_idle()
            changed = HookEvent("custom", "event", "one", payload={"retry_num": 1})
            assert await runner.submit([(hook, changed)]) == 0
            [record] = await runner.inspect()
            assert (record.status, record.attempt) == ("successful", 2)
            assert calls == [{"retry_num": 0}, {"retry_num": 0}]
        finally:
            await runner.close()

    run(scenario())
