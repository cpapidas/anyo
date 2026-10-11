"""Real-file queue contracts and deterministic multi-process recovery checks."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from anyo.execution import (
    CapacityError,
    Job,
    QueueBackend,
    QueueMismatch,
    QueueSettings,
    QueueUnavailable,
)
from anyo.integrations.sqlite import SqliteQueue

REGISTRY = {"one": "fingerprint-one", "two": "fingerprint-two"}


def run(coroutine: Coroutine[Any, Any, None]) -> None:
    async def bounded() -> None:
        await asyncio.wait_for(coroutine, 20)

    asyncio.run(bounded())


def job(identifier: str, hook: str = "one", **kwargs: Any) -> Job:
    return Job(identifier, hook, REGISTRY[hook], '{"value":1}', **kwargs)


def url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'queue.db'}"


@asynccontextmanager
async def opened(
    tmp_path: Path,
    *,
    settings: QueueSettings | None = None,
    namespace: str = "test",
    now: list[float] | None = None,
) -> AsyncIterator[SqliteQueue]:
    queue = SqliteQueue(url(tmp_path), namespace, settings or QueueSettings())
    if now is not None:
        queue._now = lambda connection: now[0]  # type: ignore[method-assign]
    await queue.open(REGISTRY)
    try:
        yield queue
    finally:
        await queue.close()


def test_restart_preserves_pending_work_and_delivery_deduplication(tmp_path):
    async def scenario():
        async with opened(tmp_path) as queue:
            assert await queue.admit([job("one")]) == 1
        async with opened(tmp_path) as queue:
            assert await queue.admit([job("one")]) == 0
            claimed = await queue.claim("worker")
            assert claimed is not None and claimed.job == job("one")
            assert claimed.attempt == 1
            assert await queue.finish(claimed, "successful")
        async with opened(tmp_path) as queue:
            assert await queue.claim("worker") is None
            assert await queue.admit([job("one")]) == 0
            assert (await queue.inspect())[0].status == "successful"

    run(scenario())


def test_fanout_is_atomic_and_partial_success_is_preserved(tmp_path):
    async def scenario():
        async with opened(tmp_path, settings=QueueSettings(capacity=2)) as queue:
            with pytest.raises(CapacityError):
                await queue.admit([job("a"), job("b"), job("c")])
            assert await queue.inspect() == []
            first, second = job("delivery-one"), job("delivery-two", "two")
            assert await queue.admit([first, second, first]) == 2
            one = await queue.claim("worker")
            two = await queue.claim("worker")
            assert one is not None and two is not None
            assert one.job == first and two.job == second
            assert await queue.finish(one, "successful")
            assert await queue.finish(two, "failed", error="anyo.pipeline_failed")
            assert await queue.admit([first, second]) == 1
            replay = await queue.claim("worker")
            assert replay is not None and replay.job == second and replay.attempt == 1
            assert not await queue.finish(two, "successful")

    run(scenario())


def test_capacity_and_history_are_shared_across_connections(tmp_path):
    async def scenario():
        settings = QueueSettings(capacity=1, max_records=1)
        async with opened(tmp_path, settings=settings) as first:
            async with opened(tmp_path, settings=settings) as second:
                await first.admit([job("a")])
                with pytest.raises(CapacityError):
                    await second.admit([job("b")])
                claim = await second.claim("other-process")
                assert claim is not None
                with pytest.raises(CapacityError):
                    await first.admit([job("b")])
                await second.finish(claim, "successful")
                with pytest.raises(CapacityError, match="history"):
                    await first.admit([job("b")])
                assert await first.admit([job("a")]) == 0

    run(scenario())


def test_history_retention_never_prunes_queued_or_delayed_jobs(tmp_path):
    async def scenario():
        now = [100.0]
        async with opened(tmp_path, settings=QueueSettings(retention_seconds=5), now=now) as queue:
            await queue.admit([job("terminal"), job("retry", max_attempts=2, retry_delay=20)])
            terminal = await queue.claim("worker")
            retry = await queue.claim("worker")
            assert terminal is not None and retry is not None
            await queue.finish(terminal, "successful")
            await queue.finish(retry, "failed", retry=True)
            now[0] = 106
            assert [(row.id, row.status) for row in await queue.inspect()] == [("retry", "queued")]
            assert await queue.claim("worker") is None
            now[0] = 120
            retry = await queue.claim("worker")
            assert retry is not None and retry.attempt == 2

    run(scenario())


def test_namespace_isolation(tmp_path):
    async def scenario():
        settings = QueueSettings(capacity=1, max_records=1)
        async with opened(tmp_path, settings=settings, namespace="a") as first:
            async with opened(tmp_path, settings=settings, namespace="b") as second:
                assert await first.admit([job("same-id")]) == 1
                assert await second.admit([job("same-id")]) == 1
                a, b = await first.claim("a"), await second.claim("b")
                assert a is not None and b is not None
                assert not await first.finish(b, "successful")
                assert await first.finish(a, "successful")
                assert (await second.inspect())[0].status == "running"

    run(scenario())


@pytest.mark.parametrize("setting", ["capacity", "max_records", "retention_seconds"])
def test_pinned_settings_reject_incompatible_processes(tmp_path, setting):
    async def scenario():
        async with opened(tmp_path):
            queue = SqliteQueue(url(tmp_path), "test", replace(QueueSettings(), **{setting: 2}))
            try:
                with pytest.raises(QueueMismatch):
                    await queue.open(REGISTRY)
            finally:
                await queue.close()

    run(scenario())


def test_registry_order_is_irrelevant_but_definitions_and_schema_must_match(tmp_path):
    async def scenario():
        async with opened(tmp_path):
            queue = SqliteQueue(url(tmp_path), "test", QueueSettings())
            await queue.open(dict(reversed(list(REGISTRY.items()))))
            await queue.close()
            for registry in ({"one": "changed", "two": REGISTRY["two"]}, {"one": REGISTRY["one"]}):
                queue = SqliteQueue(url(tmp_path), "test", QueueSettings())
                with pytest.raises(QueueMismatch):
                    await queue.open(registry)
                await queue.close()
        with sqlite3.connect(tmp_path / "queue.db") as connection:
            connection.execute("UPDATE anyo_namespaces SET schema_version=999")
        queue = SqliteQueue(url(tmp_path), "test", QueueSettings())
        with pytest.raises(QueueMismatch):
            await queue.open(REGISTRY)
        await queue.close()

    run(scenario())


def test_redelivery_rejects_changed_job_definitions_without_partial_writes(tmp_path):
    async def scenario():
        async with opened(tmp_path) as queue:
            await queue.admit([job("existing")])
            with pytest.raises(QueueMismatch):
                await queue.admit([job("new"), replace(job("existing"), max_attempts=2)])
            assert [row.id for row in await queue.inspect()] == ["existing"]
            with pytest.raises(QueueMismatch):
                await queue.admit([replace(job("unknown"), hook_id="unknown")])
            with pytest.raises(QueueMismatch):
                await queue.admit([job("new"), replace(job("new"), retry_delay=2)])

    run(scenario())


def test_redelivery_preserves_first_event_despite_transport_metadata_and_key_order(tmp_path):
    async def scenario():
        original = replace(job("delivery"), event='{"payload":{"a":1,"b":2},"retry_num":null}')
        redelivery = replace(job("delivery"), event='{"retry_num":1,"payload":{"b":2,"a":1}}')
        async with opened(tmp_path) as queue:
            assert await queue.admit([original, redelivery]) == 1
            assert await queue.admit([redelivery]) == 0
            claimed = await queue.claim("worker")
            assert claimed is not None and claimed.job.event == original.event
            assert await queue.admit([redelivery]) == 0
            await queue.finish(claimed, "failed")
            assert await queue.admit([redelivery]) == 1
            replay = await queue.claim("worker")
            assert replay is not None and replay.job.event == original.event
            await queue.finish(replay, "successful")
            assert await queue.admit([redelivery]) == 0

    run(scenario())


def test_delayed_retry_order_backoff_and_exhaustion(tmp_path):
    async def scenario():
        now = [100.0]
        async with opened(tmp_path, now=now) as queue:
            await queue.admit([job("retry", max_attempts=3, retry_delay=2), job("next")])
            claimed = await queue.claim("worker")
            assert claimed is not None and claimed.job.id == "retry"
            assert await queue.finish(claimed, "failed", retry=True, error="anyo.pipeline_failed")
            next_job = await queue.claim("worker")
            assert next_job is not None and next_job.job.id == "next"
            assert await queue.claim("worker") is None
            now[0] = 102
            claimed = await queue.claim("worker")
            assert claimed is not None and claimed.attempt == 2
            await queue.finish(claimed, "failed", retry=True)
            assert (await queue.inspect())[0].available_at == 106
            now[0] = 106
            claimed = await queue.claim("worker")
            assert claimed is not None and claimed.attempt == 3
            await queue.finish(claimed, "failed", retry=True)
            assert (await queue.inspect())[0].status == "failed"
            assert await queue.claim("worker") is None

    run(scenario())


def test_backoff_is_capped(tmp_path):
    async def scenario():
        now = [100.0]
        async with opened(tmp_path, now=now) as queue:
            await queue.admit([job("retry", max_attempts=2, retry_delay=10000)])
            claimed = await queue.claim("worker")
            assert claimed is not None
            await queue.finish(claimed, "failed", retry=True)
            assert (await queue.inspect())[0].available_at == 3700

    run(scenario())


@pytest.mark.parametrize("retry_interrupted", [False, True])
def test_expired_lease_is_interrupted_unless_replay_is_explicit(tmp_path, retry_interrupted):
    async def scenario():
        now = [100.0]
        async with opened(tmp_path, now=now, settings=QueueSettings(lease_seconds=5)) as queue:
            await queue.admit(
                [job("a", max_attempts=2, retry_delay=0, retry_interrupted=retry_interrupted)]
            )
            first = await queue.claim("old-worker")
            assert first is not None
            now[0] = 103
            assert await queue.renew(first)
            now[0] = 106
            assert await queue.claim("other-worker") is None
            now[0] = 108
            assert not await queue.renew(first)
            assert not await queue.finish(first, "successful")
            second = await queue.claim("replacement-worker")
            if retry_interrupted:
                assert second is not None and second.attempt == 2 and second.token != first.token
                assert not await queue.finish(first, "failed")
                now[0] = 113
                assert await queue.claim("worker") is None
            else:
                assert second is None
            info = (await queue.inspect())[0]
            assert info.status == "interrupted" and info.error == "anyo.lease_expired"

    run(scenario())


def test_interrupted_finish_requires_distinct_retry_opt_in(tmp_path):
    async def scenario():
        async with opened(tmp_path) as queue:
            await queue.admit([job("a", max_attempts=2, retry_delay=0)])
            claimed = await queue.claim("worker")
            assert claimed is not None
            await queue.finish(claimed, "interrupted", error="anyo.outcome_unknown", retry=True)
            assert await queue.claim("worker") is None
            assert (await queue.inspect())[0].status == "interrupted"
            assert await queue.retry("a")
            retried = await queue.claim("worker")
            assert retried is not None and retried.attempt == 1
            assert not await queue.finish(claimed, "successful")

    run(scenario())


def test_manual_retry_obeys_capacity_and_terminal_state(tmp_path):
    async def scenario():
        async with opened(tmp_path, settings=QueueSettings(capacity=1)) as queue:
            await queue.admit([job("failed")])
            claim = await queue.claim("worker")
            assert claim is not None
            await queue.finish(claim, "failed")
            await queue.admit([job("queued")])
            assert not await queue.retry("queued")
            assert not await queue.retry("missing")
            with pytest.raises(CapacityError):
                await queue.retry("failed")

    run(scenario())


def test_memory_database_and_relative_url_have_explicit_lifetimes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def scenario():
        queue: QueueBackend = SqliteQueue("sqlite:///:memory:", "test", QueueSettings())
        await queue.open(REGISTRY)
        await queue.admit([job("a")])
        assert len(await queue.inspect()) == 1
        await queue.close()
        await queue.close()
        with pytest.raises(QueueUnavailable, match="closed"):
            await queue.inspect()
        relative = SqliteQueue("sqlite:///nested/queue.db", "test", QueueSettings())
        assert not (tmp_path / "nested").exists()
        await relative.open(REGISTRY)
        assert (tmp_path / "nested" / "queue.db").is_file()
        await relative.close()

    run(scenario())


def test_unavailable_path_is_actionable_and_does_not_leak_connection(tmp_path):
    async def scenario():
        queue = SqliteQueue(f"sqlite:///{tmp_path}", "test", QueueSettings())
        with pytest.raises(QueueUnavailable, match="path, permissions"):
            await queue.open(REGISTRY)
        await queue.close()

    run(scenario())


def test_sqlite_waits_are_bounded_and_do_not_block_async_tasks(tmp_path, monkeypatch):
    async def scenario():
        async with opened(tmp_path, settings=QueueSettings(operation_timeout=0.1)) as queue:
            with sqlite3.connect(tmp_path / "queue.db", isolation_level=None) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                started = threading.Event()
                original_run = queue._run

                def signaled(*args):
                    started.set()
                    return original_run(*args)

                monkeypatch.setattr(queue, "_run", signaled)
                admission = asyncio.create_task(queue.admit([job("a")]))
                assert await asyncio.to_thread(started.wait, 2)
                pulse = asyncio.Event()
                asyncio.get_running_loop().call_soon(pulse.set)
                await asyncio.wait_for(pulse.wait(), 1)
                with pytest.raises(QueueUnavailable):
                    await admission
                blocker.rollback()
            assert await queue.inspect() == []
            assert await queue.admit([job("a")]) == 1

    run(scenario())


def test_cancelled_admission_releases_lock_and_remains_safe_to_resubmit(tmp_path, monkeypatch):
    async def scenario():
        async with opened(tmp_path) as queue:
            entered, release = threading.Event(), threading.Event()
            original_admit = queue._admit

            def paused(connection, jobs):
                entered.set()
                assert release.wait(2)
                return original_admit(connection, jobs)

            with monkeypatch.context() as patch:
                patch.setattr(queue, "_admit", paused)
                task = asyncio.create_task(queue.admit([job("a")]))
                assert await asyncio.to_thread(entered.wait, 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                release.set()
            assert await queue.admit([job("a")]) == 1
            assert len(await queue.inspect()) == 1

    run(scenario())


def test_commit_with_lost_acknowledgment_is_safe_to_submit_again(tmp_path, monkeypatch):
    async def scenario():
        async with opened(tmp_path) as queue:
            original_run = queue._run

            def lost_ack(*args):
                original_run(*args)
                raise QueueUnavailable("connection lost after commit")

            with monkeypatch.context() as patch:
                patch.setattr(queue, "_run", lost_ack)
                with pytest.raises(QueueUnavailable, match="after commit"):
                    await queue.admit([job("first"), job("second", "two")])
            assert await queue.admit([job("first"), job("second", "two")]) == 0
            assert len(await queue.inspect()) == 2

    run(scenario())


def test_cancelled_open_closes_its_connection(tmp_path, monkeypatch):
    async def scenario():
        queue = SqliteQueue(url(tmp_path), "test", QueueSettings())
        entered, release = threading.Event(), threading.Event()
        original_initialize = queue._initialize

        def paused(connection, registry):
            entered.set()
            assert release.wait(2)
            original_initialize(connection, registry)

        monkeypatch.setattr(queue, "_initialize", paused)
        task = asyncio.create_task(queue.open(REGISTRY))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert queue._connection is None
        # A separate receiver can initialize the same file after the cancellation.
        async with opened(tmp_path) as replacement:
            assert await replacement.admit([job("a")]) == 1

    run(scenario())


_WORKER = r"""
import asyncio, json, os, sys
from anyo.execution import QueueSettings
from anyo.integrations.sqlite import SqliteQueue

async def main():
    queue = SqliteQueue(sys.argv[1], "test", QueueSettings())
    await queue.open({"one": "fingerprint-one", "two": "fingerprint-two"})
    print("ready", flush=True)
    await asyncio.to_thread(sys.stdin.readline)
    claimed = await queue.claim(str(os.getpid()))
    assert claimed is not None
    print(claimed.job.id, flush=True)
    await asyncio.to_thread(sys.stdin.readline)
    identifiers = [claimed.job.id]
    await queue.finish(claimed, "successful")
    while (claimed := await queue.claim(str(os.getpid()))) is not None:
        identifiers.append(claimed.job.id)
        assert await queue.finish(claimed, "successful")
    await queue.close()
    print(json.dumps(identifiers), flush=True)

asyncio.run(main())
"""


def test_two_real_worker_processes_claim_each_job_once(tmp_path):
    async def scenario():
        async with opened(tmp_path) as queue:
            await queue.admit([job(str(number)) for number in range(30)])
            processes = []
            try:
                for _ in range(2):
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-c",
                        _WORKER,
                        url(tmp_path),
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    processes.append(process)
                for process in processes:
                    assert await process.stdout.readline() == b"ready\n"
                for process in processes:
                    process.stdin.write(b"claim\n")
                    await process.stdin.drain()
                first_claims = [await process.stdout.readline() for process in processes]
                assert len(set(first_claims)) == 2
                for process in processes:
                    process.stdin.write(b"finish\n")
                    await process.stdin.drain()
                results = await asyncio.gather(*(process.communicate() for process in processes))
                identifiers = []
                for process, (stdout, stderr) in zip(processes, results, strict=True):
                    assert process.returncode == 0, stderr.decode()
                    identifiers.extend(json.loads(stdout))
                assert len(identifiers) == 30 and set(identifiers) == {str(n) for n in range(30)}
                assert {row.status for row in await queue.inspect()} == {"successful"}
            finally:
                for process in processes:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()

    run(scenario())


_CRASH_WORKER = r"""
import asyncio, json, os, sys
from pathlib import Path
from anyo.execution import QueueSettings
from anyo.integrations.sqlite import SqliteQueue

async def main():
    queue = SqliteQueue(sys.argv[1], "test", QueueSettings())
    await queue.open({"one": "fingerprint-one", "two": "fingerprint-two"})
    claimed = await queue.claim(str(os.getpid()))
    assert claimed is not None
    if sys.argv[2] == "after":
        Path(sys.argv[3]).write_text("external effect")
    info = (await queue.inspect())[0]
    print(json.dumps({"id": claimed.job.id, "lease": info.lease_until}), flush=True)
    await asyncio.to_thread(sys.stdin.readline)

asyncio.run(main())
"""


@pytest.mark.parametrize("crash_at", ["before", "after"])
def test_killed_worker_records_uncertainty_before_or_after_external_effect(tmp_path, crash_at):
    async def scenario():
        async with opened(tmp_path) as queue:
            await queue.admit([job("work", max_attempts=3)])
        effect = tmp_path / "effect.txt"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _CRASH_WORKER,
            url(tmp_path),
            crash_at,
            str(effect),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            line = await process.stdout.readline()
            if not line:
                raise AssertionError((await process.stderr.read()).decode())
            record = json.loads(line)
            process.kill()
            await process.wait()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        async with opened(tmp_path, now=[record["lease"] + 1]) as queue:
            assert await queue.claim("replacement") is None
            info = (await queue.inspect())[0]
            assert info.status == "interrupted" and info.attempt == 1
            assert effect.exists() == (crash_at == "after")
            assert await queue.retry("work")
            claimed = await queue.claim("authorized-replay")
            assert claimed is not None and claimed.attempt == 1

    run(scenario())
