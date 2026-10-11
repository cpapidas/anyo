"""Durable operations contracts, using real SQLite and explicitly selected Redis."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest

from anyo.execution._contracts import Job, QueueSettings
from anyo.execution._operations import LogEntry, OperationsSettings, WorkerInfo
from anyo.integrations.redis import RedisQueue
from anyo.integrations.sqlite import SqliteQueue


def run(awaitable):
    async def bounded():
        async with asyncio.timeout(15):
            await awaitable

    asyncio.run(bounded())


@pytest.fixture(params=["sqlite", pytest.param("redis", marks=pytest.mark.live)])
def backend(request, tmp_path):
    if request.param == "redis":
        endpoint = os.environ.get("ANYO_TEST_REDIS_URL")
        if not endpoint:
            pytest.skip("Select a test Redis with ANYO_TEST_REDIS_URL and -m live")
        pytest.importorskip("redis.asyncio")
        return RedisQueue, endpoint
    return SqliteQueue, f"sqlite:///{tmp_path / 'operations.sqlite3'}"


@asynccontextmanager
async def queues(backend, *, settings=None):
    factory, endpoint = backend
    namespace = "operations-test-" + uuid4().hex
    first = factory(endpoint, namespace, QueueSettings())
    second = factory(endpoint, namespace, QueueSettings())
    other = factory(endpoint, namespace + "-other", QueueSettings())
    clients = (first, second, other)
    try:
        for client in clients:
            client.configure_operations(settings or OperationsSettings())
            await client.open({"hook": "fingerprint"})
        yield clients
    finally:
        for client in clients:
            await client.close()
        if factory is RedisQueue:
            import redis.asyncio

            admin = redis.asyncio.Redis.from_url(endpoint)
            await admin.delete(*first._operations_keys, *other._operations_keys)
            await admin.aclose()


def job(identifier):
    return Job(
        identifier,
        "hook",
        "fingerprint",
        "{}",
        max_attempts=3,
        retry_delay=0,
        retry_interrupted=True,
    )


async def expire_claim(queue, claim):
    if isinstance(queue, SqliteQueue):
        with sqlite3.connect(queue._path) as db:
            db.execute(
                "UPDATE anyo_jobs SET lease_until=0 WHERE namespace=? AND id=?",
                (queue._namespace, claim.job.id),
            )
    else:
        await queue._execute(
            "EVAL",
            """
            local item = cjson.decode(redis.call('HGET', KEYS[1], ARGV[1]))
            item.lease_until = 0
            redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(item))
            redis.call('ZADD', KEYS[2], 0, ARGV[1])
            return 1
        """,
            2,
            queue._keys[1],
            queue._keys[3],
            claim.job.id,
        )


def test_queued_cancel_is_terminal_deduplicated_and_explicitly_retryable(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, other):
            await receiver.admit([job("run")])
            assert await receiver.cancel("run")
            assert await receiver.cancel("run", force=True)
            assert not await other.cancel("run")
            assert await worker.claim("worker") is None
            assert await receiver.admit([job("run")]) == 0
            info = (await receiver.inspect())[0]
            assert info.status == "cancelled" and info.finished_at is not None
            assert info.created_at is not None and info.started_at is None
            assert await receiver.retry("run")
            claim = await worker.claim("worker")
            assert claim is not None and claim.attempt == 1
            assert await worker.control(claim) is None
            assert await worker.finish(claim, "successful")
            assert not await receiver.cancel("run", force=True)

    run(scenario())


def test_run_requests_are_attempt_fenced_and_force_escalates(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, _):
            await receiver.admit([job("run")])
            claim = await worker.claim("worker")
            assert claim is not None
            assert await receiver.cancel("run")
            assert await worker.control(claim) == "cancel"
            assert await receiver.cancel("run", force=True)
            assert await receiver.cancel("run")
            assert await worker.control(claim) == "force_stop"
            info = (await receiver.inspect())[0]
            assert info.status == "running" and info.owner == "worker"
            assert info.control_requested_at is not None and info.started_at is not None
            assert await worker.control(replace(claim, token="stale")) is None
            assert await worker.finish(claim, "cancelled", retry=True)
            assert (await receiver.inspect())[0].status == "cancelled"
            assert await receiver.retry("run")
            current = await worker.claim("worker")
            assert current is not None and current.token != claim.token
            assert await worker.control(current) is None
            assert not await worker.finish(claim, "successful")

    run(scenario())


def test_lost_cancel_owner_is_unconfirmed_and_never_automatically_replayed(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, _):
            await receiver.admit([job("run")])
            claim = await worker.claim("worker")
            assert claim is not None
            assert await receiver.cancel("run")
            await expire_claim(worker, claim)
            assert await worker.claim("replacement") is None
            info = (await receiver.inspect())[0]
            assert info.status == "interrupted" and info.error == "anyo.cancel_unconfirmed"
            assert await receiver.admit([job("run")]) == 0
            assert await receiver.retry("run")
            assert await worker.claim("replacement") is not None

    run(scenario())


def test_claim_and_finish_racing_cancel_have_one_consistent_outcome(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, _):
            for i in range(8):
                identifier = f"run-{i}"
                await receiver.admit([job(identifier)])
                requested, claim = await asyncio.gather(
                    receiver.cancel(identifier), worker.claim("worker")
                )
                assert requested
                if claim is not None:
                    assert claim.job.id == identifier
                    assert await worker.control(claim) == "cancel"
                    assert await worker.finish(claim, "cancelled")
                assert (await receiver.inspect())[i].status == "cancelled"
            await receiver.admit([job("completion")])
            claim = await worker.claim("worker")
            assert claim is not None
            finished, requested = await asyncio.gather(
                worker.finish(claim, "successful"), receiver.cancel("completion")
            )
            assert finished
            assert (await receiver.inspect())[-1].status == "successful"
            assert await worker.control(claim) is None

    run(scenario())


def test_worker_commands_survive_heartbeat_and_preserve_namespace(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, other):
            record = WorkerInfo(
                "worker",
                execution_mode="process",
                active_run_ids=("run",),
                started_at=123,
                hostname="fixture",
                pid=123,
            )
            await worker.worker_heartbeat(record)
            assert (await receiver.workers())[0].active_run_ids == ("run",)
            assert await receiver.worker_command("worker")
            await worker.worker_heartbeat(replace(record, state="draining"))
            assert await worker.get_worker_command("worker") == "drain"
            assert (await receiver.workers())[0].command == "drain"
            assert await receiver.worker_command("worker", force=True)
            assert await receiver.worker_command("worker")
            await worker.worker_heartbeat(replace(record, state="stopped", active_run_ids=()))
            records = await receiver.workers()
            assert records[0].command == "force_stop"
            assert records[0].state == "stopped" and records[0].active_run_ids == ()
            assert records[0].heartbeat_at > 123
            assert not await receiver.worker_command("worker")
            assert await other.workers() == []
            assert not await other.worker_command("worker")
            assert await other.get_worker_command("worker") is None

    run(scenario())


def test_logs_are_bounded_filtered_ordered_and_report_retention_gaps(backend):
    async def scenario():
        settings = OperationsSettings(log_max_entries=3, max_log_bytes=5)
        async with queues(backend, settings=settings) as (receiver, worker, other):
            assert (await receiver.logs()).entries == []
            await worker.append_logs(
                [
                    LogEntry("first", run_id="one", source="stdout"),
                    LogEntry("αβγδε", run_id="two", source="omo"),
                ]
            )
            page = await receiver.logs(limit=1)
            assert [entry.message for entry in page.entries] == ["first"]
            assert page.next_cursor == 1 and page.oldest_cursor == 1 and not page.gap
            page = await receiver.logs(after=page.next_cursor)
            assert page.entries[0].message == "αβ" and page.next_cursor == 2
            await worker.append_logs(
                [
                    LogEntry("third", run_id="one", level="ERROR"),
                    LogEntry("last", worker_id="worker"),
                ]
            )
            page = await receiver.logs(after=0)
            assert [entry.cursor for entry in page.entries] == [2, 3, 4]
            assert page.gap and page.oldest_cursor == 2
            assert not (await receiver.logs(after=1)).gap
            assert [e.message for e in (await receiver.logs(run_id="one")).entries] == ["third"]
            assert [e.message for e in (await receiver.logs(worker_id="worker")).entries] == [
                "last"
            ]
            assert [e.message for e in (await receiver.logs(source="omo")).entries] == ["αβ"]
            assert len((await receiver.logs(level="ERROR")).entries) == 1
            empty = await receiver.logs(source="missing")
            assert empty.entries == [] and empty.next_cursor == 4
            assert (await other.logs()).entries == []
            with pytest.raises(ValueError):
                await receiver.logs(after=-1)

    run(scenario())


def test_logs_and_worker_retention_expire_without_new_writes(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, _):
            await worker.append_logs([LogEntry("retained")])
            await worker.worker_heartbeat(WorkerInfo("old"))
            if isinstance(receiver, SqliteQueue):
                with sqlite3.connect(receiver._path) as db:
                    db.execute("UPDATE anyo_logs SET received_at=0")
                    db.execute("UPDATE anyo_workers SET heartbeat_at=0")
            else:
                await receiver._execute("ZADD", receiver._operations_keys[10], 0, "1")
                await receiver._execute("ZADD", receiver._operations_keys[7], 0, "old")
            page = await receiver.logs()
            assert page.entries == [] and page.gap and page.next_cursor == 1
            assert page.oldest_cursor is None
            assert await receiver.workers() == []

    run(scenario())


def test_sqlite_operations_survive_restart_and_cross_process_controls(tmp_path):
    async def scenario():
        endpoint = f"sqlite:///{tmp_path / 'process.sqlite3'}"
        queue = SqliteQueue(endpoint, "process-test", QueueSettings())
        await queue.open({"hook": "fingerprint"})
        await queue.admit([job("run")])
        claim = await queue.claim("worker")
        await queue.worker_heartbeat(WorkerInfo("worker"))
        code = """
import asyncio, sys
from anyo.integrations.sqlite import SqliteQueue
from anyo.execution._contracts import QueueSettings
from anyo.execution._operations import LogEntry
async def main():
    q = SqliteQueue(sys.argv[1], 'process-test', QueueSettings())
    await q.open({'hook': 'fingerprint'})
    assert await q.cancel('run', force=True)
    assert await q.worker_command('worker', force=True)
    await q.append_logs([LogEntry('child diagnostic', run_id='run')])
    await q.close()
asyncio.run(main())
"""
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code, endpoint, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        assert await queue.control(claim) == "force_stop"
        assert await queue.get_worker_command("worker") == "force_stop"
        await queue.close()
        queue = SqliteQueue(endpoint, "process-test", QueueSettings())
        await queue.open({"hook": "fingerprint"})
        try:
            assert await queue.control(claim) == "force_stop"
            assert (await queue.logs()).entries[0].message == "child diagnostic"
            assert (await queue.workers())[0].command == "force_stop"
        finally:
            await queue.close()

    run(scenario())


def test_sqlite_additive_schema_upgrade_keeps_registry_and_existing_jobs(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE anyo_jobs (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL,
            id TEXT NOT NULL, hook_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            event TEXT NOT NULL, max_attempts INTEGER NOT NULL, retry_delay REAL NOT NULL,
            retry_interrupted INTEGER NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL,
            available_at REAL NOT NULL, owner TEXT, token TEXT, lease_until REAL,
            finished_at REAL, error TEXT, UNIQUE(namespace, id))""")
        db.execute("""INSERT INTO anyo_jobs(namespace,id,hook_id,fingerprint,event,max_attempts,
            retry_delay,retry_interrupted,status,attempt,available_at)
            VALUES('migration','old','hook','fingerprint','{}',1,1,0,'queued',0,123)""")

    async def scenario():
        queue = SqliteQueue(f"sqlite:///{path}", "migration", QueueSettings())
        await queue.open({"hook": "fingerprint"})
        try:
            info = (await queue.inspect())[0]
            assert info.created_at == 123 and info.id == "old"
            assert info.attempt_token
            assert (await queue.inspect())[0].attempt_token == info.attempt_token
            assert await queue.cancel("old", expected_token=info.attempt_token)
            assert await queue.claim("worker") is None
        finally:
            await queue.close()

    run(scenario())


def test_stop_command_atomically_prevents_further_claims(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, _):
            await worker.worker_heartbeat(WorkerInfo("worker"))
            await receiver.admit([job("first"), job("second")])
            active = await worker.claim("worker")
            assert active is not None
            assert await receiver.worker_command("worker")
            assert await worker.claim("worker") is None
            assert await worker.finish(active, "successful")
            sibling = await worker.claim("different-worker")
            assert sibling is not None and sibling.job.id != active.job.id

    run(scenario())


def test_pending_control_never_automatically_retries_a_failure(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, _):
            await receiver.admit([job("run")])
            claim = await worker.claim("worker")
            assert claim is not None
            assert await receiver.cancel("run")
            assert await worker.finish(claim, "failed", retry=True)
            assert (await receiver.inspect())[0].status == "failed"
            assert await worker.claim("worker") is None
            assert await receiver.admit([job("run")]) == 0
            assert await receiver.retry("run")
            assert await worker.claim("worker") is not None

    run(scenario())


def test_sqlite_control_failure_rolls_back_and_can_recover(tmp_path):
    async def scenario():
        endpoint = f"sqlite:///{tmp_path / 'locked.sqlite3'}"
        settings = QueueSettings(operation_timeout=0.05)
        queue = SqliteQueue(endpoint, "lock-test", settings)
        await queue.open({"hook": "fingerprint"})
        await queue.admit([job("run")])
        blocker = sqlite3.connect(tmp_path / "locked.sqlite3")
        blocker.execute("BEGIN IMMEDIATE")
        try:
            from anyo.execution._contracts import QueueUnavailable

            with pytest.raises(QueueUnavailable):
                await queue.cancel("run")
        finally:
            blocker.rollback()
            blocker.close()
        try:
            info = (await queue.inspect())[0]
            assert info.status == "queued" and info.control is None
            assert await queue.cancel("run")
            assert (await queue.inspect())[0].status == "cancelled"
        finally:
            await queue.close()

    run(scenario())


@pytest.mark.live
def test_redis_operations_acl_failure_leaves_history_intact():
    endpoint = os.environ.get("ANYO_TEST_REDIS_URL")
    if not endpoint:
        pytest.skip("Select a test Redis with ANYO_TEST_REDIS_URL and -m live")
    from urllib.parse import urlsplit, urlunsplit

    import redis.asyncio

    from anyo.execution._contracts import QueueUnavailable

    async def scenario():
        async with queues((RedisQueue, endpoint)) as (owner, _, _):
            admin = redis.asyncio.Redis.from_url(endpoint, decode_responses=True)
            username = "anyo-operations-acl-" + uuid4().hex
            password = uuid4().hex
            limited = None
            try:
                await owner.append_logs([LogEntry("existing")])
                await admin.execute_command(
                    "ACL",
                    "SETUSER",
                    username,
                    "on",
                    ">" + password,
                    "allcommands",
                    "allkeys",
                    "-zadd",
                )
                parsed = urlsplit(endpoint)
                url = urlunsplit(
                    (parsed.scheme, f"{username}:{password}@{parsed.netloc}", parsed.path, "", "")
                )
                limited = RedisQueue(url, "unused", QueueSettings())
                # A fresh client using the exact namespace, with restricted transport credentials.
                limited._keys = owner._keys
                limited._operations_keys = owner._operations_keys
                with pytest.raises(QueueUnavailable):
                    await limited.open({"hook": "fingerprint"})
                # The baseline queue validates grants on open; reconnect after open then restrict.
                await admin.execute_command("ACL", "SETUSER", username, "+zadd")
                limited = RedisQueue(url, "unused", QueueSettings())
                limited._keys = owner._keys
                limited._operations_keys = owner._operations_keys
                await limited.open({"hook": "fingerprint"})
                await admin.execute_command("ACL", "SETUSER", username, "-zadd")
                with pytest.raises(QueueUnavailable):
                    await limited.append_logs([LogEntry("must not persist")])
                assert [item.message for item in (await owner.logs()).entries] == ["existing"]
            finally:
                if limited is not None:
                    await limited.close()
                await admin.execute_command("ACL", "DELUSER", username)
                await admin.aclose()

    run(scenario())


def test_worker_force_stop_atomically_prevents_replay_after_supervisor_loss(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, _):
            await worker.worker_heartbeat(WorkerInfo("worker"))
            await receiver.admit([job("selected"), job("sibling"), job("queued")])
            selected = await worker.claim("worker:0")
            sibling = await worker.claim("worker-other:0")
            assert selected is not None and sibling is not None
            assert await receiver.worker_command("worker", force=True)
            assert await worker.control(selected) == "force_stop"
            assert await worker.control(sibling) is None
            assert await worker.claim("worker:1") is None
            await expire_claim(worker, selected)
            info = {record.id: record for record in await receiver.inspect()}
            assert info["selected"].status == "interrupted"
            assert info["selected"].error == "anyo.cancel_unconfirmed"
            assert info["sibling"].status == "running"
            claim = await worker.claim("different:0")
            assert claim is not None and claim.job.id == "queued"

    run(scenario())


@pytest.mark.live
def test_redis_operations_survive_disposable_server_restart():
    endpoint = os.environ.get("ANYO_TEST_REDIS_URL")
    container = os.environ.get("ANYO_TEST_REDIS_CONTAINER")
    if not endpoint or not container:
        pytest.skip("Set test Redis URL and explicit disposable container for restart verification")
    if not container.startswith("anyo-worker-redis-check"):
        pytest.fail("Restart only a disposable anyo-worker-redis-check* container")
    from anyo.execution._contracts import QueueUnavailable

    async def docker(*args):
        process = await asyncio.create_subprocess_exec(
            os.environ.get("ANYO_TEST_DOCKER", "/usr/local/bin/docker"),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, errors = await process.communicate()
        assert process.returncode == 0, errors.decode()

    async def scenario():
        async with queues((RedisQueue, endpoint)) as (receiver, worker, _):
            await receiver.admit([job("run")])
            claim = await worker.claim("worker:0")
            assert claim is not None
            await worker.worker_heartbeat(WorkerInfo("worker", active_run_ids=("run",)))
            await receiver.worker_command("worker", force=True)
            await worker.append_logs([LogEntry("before restart", run_id="run")])
            await docker("stop", "--time", "5", container)
            try:
                with pytest.raises(QueueUnavailable):
                    await receiver.logs()
            finally:
                await docker("start", container)
            for _ in range(100):
                try:
                    page = await receiver.logs()
                    break
                except QueueUnavailable:
                    await asyncio.sleep(0.02)
            else:
                pytest.fail("Redis did not become ready after restart")
            assert [entry.message for entry in page.entries] == ["before restart"]
            assert (await receiver.workers())[0].command == "force_stop"
            assert await receiver.control(claim) == "force_stop"
            assert await receiver.claim("worker:1") is None

    run(scenario())


def test_ui_token_precondition_never_cancels_a_new_attempt(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, _):
            await receiver.admit([job("run")])
            queued_token = (await receiver.inspect())[0].attempt_token
            assert queued_token
            first = await worker.claim("worker:0")
            assert first is not None
            assert (await receiver.inspect())[0].attempt_token == first.token
            assert not await receiver.cancel("run", expected_token=queued_token)
            assert await worker.control(first) is None
            assert await worker.finish(first, "failed")
            assert await receiver.retry("run")
            assert not await receiver.cancel("run", expected_token=first.token)
            second = await worker.claim("another-worker:0")
            assert second is not None and second.attempt == first.attempt
            assert not await receiver.cancel("run", force=True, expected_token=first.token)
            assert await worker.control(second) is None
            assert await receiver.cancel("run", force=True, expected_token=second.token)
            assert await worker.control(second) == "force_stop"
            assert await worker.finish(second, "cancelled")
            assert (await receiver.inspect())[0].attempt_token is None
            await receiver.retry("run")
            queued_token = (await receiver.inspect())[0].attempt_token
            assert queued_token
            assert await receiver.cancel("run", expected_token=queued_token)
            assert await worker.claim("worker:0") is None

    run(scenario())


def test_queued_revision_changes_on_retry_and_old_confirmation_cannot_cancel(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, _):
            await receiver.admit([job("run")])
            original = (await receiver.inspect())[0].attempt_token
            assert original
            assert (await worker.inspect())[0].attempt_token == original
            assert await receiver.cancel("run", expected_token=original)
            assert (await receiver.inspect())[0].attempt_token is None
            assert await receiver.retry("run")
            current = (await receiver.inspect())[0].attempt_token
            assert current and current != original
            assert not await receiver.cancel("run", expected_token=original)
            assert not await receiver.cancel("run", force=True, expected_token="")
            assert (await receiver.inspect())[0].status == "queued"
            assert await receiver.cancel("run", expected_token=current)
            assert await worker.claim("worker") is None

    run(scenario())


def test_queued_revision_changes_on_automatic_retry_and_expired_lease_recovery(backend):
    async def scenario():
        async with queues(backend) as (receiver, worker, _):
            await receiver.admit([job("run")])
            first = await worker.claim("worker")
            assert first is not None
            assert await worker.finish(first, "failed", retry=True)
            requeued = (await receiver.inspect())[0].attempt_token
            assert requeued and requeued != first.token
            assert not await receiver.cancel("run", expected_token=first.token)
            second = await worker.claim("worker")
            assert second is not None
            await expire_claim(worker, second)
            recovered = (await receiver.inspect())[0]
            assert recovered.status == "queued"
            assert recovered.attempt_token and recovered.attempt_token != requeued
            assert recovered.attempt_token != second.token
            assert not await receiver.cancel("run", expected_token=requeued)
            assert await receiver.cancel("run", expected_token=recovered.attempt_token)

    run(scenario())


@pytest.mark.live
def test_redis_legacy_queued_record_gets_stable_operation_revision():
    endpoint = os.environ.get("ANYO_TEST_REDIS_URL")
    if not endpoint:
        pytest.skip("Select a test Redis with ANYO_TEST_REDIS_URL and -m live")

    async def scenario():
        async with queues((RedisQueue, endpoint)) as (receiver, worker, _):
            await receiver.admit([job("legacy")])
            await receiver._execute(
                "EVAL",
                """
                local record = cjson.decode(redis.call('HGET', KEYS[1], ARGV[1]))
                record.token = nil
                redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(record))
                return 1
            """,
                1,
                receiver._keys[1],
                "legacy",
            )
            original = (await receiver.inspect())[0].attempt_token
            assert original
            assert (await worker.inspect())[0].attempt_token == original
            assert await receiver.cancel("legacy", expected_token=original)
            assert await receiver.retry("legacy")
            assert not await receiver.cancel("legacy", expected_token=original)
            assert await worker.claim("worker") is not None

    run(scenario())
