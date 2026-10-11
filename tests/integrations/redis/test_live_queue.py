"""Explicit real Redis verification. Set ANYO_TEST_REDIS_URL and select -m live."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import pytest

from anyo.execution._contracts import (
    CapacityError,
    Job,
    QueueMismatch,
    QueueSettings,
    QueueUnavailable,
)
from anyo.integrations.redis import RedisQueue

pytestmark = pytest.mark.live


@pytest.fixture
def endpoint():
    value = os.environ.get("ANYO_TEST_REDIS_URL")
    if not value:
        pytest.skip("Set ANYO_TEST_REDIS_URL to an explicitly selected test Redis")
    pytest.importorskip("redis.asyncio")
    return value


def run(coroutine):
    async def bounded():
        async with asyncio.timeout(20):
            await coroutine

    asyncio.run(bounded())


def keys(namespace):
    prefix = "anyo:queue:{" + hashlib.sha256(namespace.encode()).hexdigest() + "}"
    return [
        f"{prefix}:{suffix}"
        for suffix in (
            "metadata",
            "jobs",
            "queued",
            "running",
            "terminal",
            "all",
        )
    ]


@asynccontextmanager
async def queues(endpoint, *, settings=None, registry=None, count=2):
    import redis.asyncio

    namespace = "anyo-live-" + uuid4().hex
    settings = settings or QueueSettings()
    registry = registry or {"hook": "fingerprint"}
    clients = [RedisQueue(endpoint, namespace, settings) for _ in range(count)]
    admin = redis.asyncio.Redis.from_url(endpoint, decode_responses=True)
    try:
        for client in clients:
            await client.open(registry)
        yield clients, admin, namespace
    finally:
        for client in clients:
            await client.close()
        await admin.delete(*keys(namespace))
        await admin.aclose()


def job(identifier, **kwargs):
    return Job(identifier, "hook", "fingerprint", '{"integer":12345678901234567890}', **kwargs)


async def move_clock_boundary(admin, namespace, identifier, field, index):
    """Move persisted deadlines, without sleeps or replacing Redis execution."""
    queue_keys = keys(namespace)
    await admin.eval(
        """
        local record = cjson.decode(redis.call('HGET', KEYS[1], ARGV[1]))
        record[ARGV[2]] = 0
        redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(record))
        redis.call('ZADD', KEYS[2], 0, ARGV[1])
    """,
        2,
        queue_keys[1],
        queue_keys[index],
        identifier,
        field,
    )


def test_atomic_fanout_capacity_dedup_and_partial_success(endpoint):
    async def scenario():
        async with queues(endpoint, settings=QueueSettings(capacity=2)) as (clients, _, _):
            receiver, worker = clients
            assert await receiver.admit([job("first"), job("first")]) == 1
            with pytest.raises(CapacityError):
                await receiver.admit([job("second"), job("third")])
            assert [item.id for item in await receiver.inspect()] == ["first"]
            assert await receiver.admit([job("second")]) == 1
            claims = await asyncio.gather(worker.claim("one"), receiver.claim("two"))
            assert all(claim is not None for claim in claims)
            assert {claim.job.id for claim in claims} == {"first", "second"}
            first, second = sorted(claims, key=lambda claim: claim.job.id)
            assert first.job.event == job("first").event
            assert await worker.finish(first, "successful")
            assert await worker.finish(second, "failed", error="pipeline.failed")
            assert await receiver.admit([job("first"), job("second")]) == 1
            only = await worker.claim("three")
            assert only.job.id == "second" and only.attempt == 1
            assert await worker.claim("four") is None

    run(scenario())


def test_registry_configuration_and_job_conflicts_preserve_entire_batch(endpoint):
    async def scenario():
        async with queues(endpoint) as (clients, admin, namespace):
            queue = clients[0]
            assert await queue.admit([job("existing")]) == 1
            with pytest.raises(QueueMismatch):
                await queue.admit([job("new"), replace(job("existing"), max_attempts=2)])
            with pytest.raises(QueueMismatch):
                await queue.admit([job("new"), replace(job("unknown"), fingerprint="v2")])
            assert [info.id for info in await queue.inspect()] == ["existing"]
            for registry, settings in [
                ({"hook": "changed"}, QueueSettings()),
                ({"hook": "fingerprint"}, QueueSettings(capacity=99)),
                ({}, QueueSettings()),
            ]:
                mismatched = RedisQueue(endpoint, namespace, settings)
                with pytest.raises(QueueMismatch):
                    await mismatched.open(registry)
                await mismatched.close()
            other = RedisQueue(endpoint, namespace + "-other", QueueSettings())
            try:
                await other.open({"hook": "fingerprint"})
                assert await other.inspect() == []
            finally:
                await other.close()
                await admin.delete(*keys(namespace + "-other"))

    run(scenario())


def test_delivery_retries_preserve_the_first_payload_despite_changed_metadata(endpoint):
    async def scenario():
        async with queues(endpoint) as (clients, _, _):
            receiver, worker = clients
            first = replace(job("delivery"), event='{"retry_num":null,"payload":{"a":1}}')
            redelivery = replace(first, event='{"payload":{"a":1},"retry_num":1}')
            assert await receiver.admit([first, redelivery]) == 1
            assert await receiver.admit([redelivery]) == 0
            claim = await worker.claim("worker")
            assert claim.job.event == first.event
            assert await receiver.admit([redelivery]) == 0
            assert await worker.finish(claim, "failed")
            assert await receiver.admit([redelivery]) == 1
            claim = await worker.claim("retried")
            assert claim.job.event == first.event
            assert await worker.finish(claim, "successful")
            assert await receiver.admit([redelivery]) == 0

    run(scenario())


def test_eligibility_preserves_admission_order_within_a_fanout(endpoint):
    async def scenario():
        async with queues(endpoint) as (clients, _, _):
            queue = clients[0]
            await queue.admit([job("z"), job("a"), job("m")])
            assert [info.id for info in await queue.inspect()] == ["z", "a", "m"]
            assert [(await queue.claim("worker")).job.id for _ in range(3)] == ["z", "a", "m"]

    run(scenario())


def test_more_workers_than_connections_can_claim_without_spurious_failures(endpoint):
    async def scenario():
        async with queues(endpoint, count=1) as (clients, _, _):
            queue = clients[0]
            await queue.admit([job(str(index)) for index in range(48)])
            claims = await asyncio.gather(*(queue.claim(str(index)) for index in range(48)))
            assert len({claim.job.id for claim in claims}) == 48
            assert all(
                await asyncio.gather(*(queue.finish(claim, "successful") for claim in claims))
            )

    run(scenario())


def test_lease_recovery_fences_old_claims_and_respects_uncertainty_policy(endpoint):
    async def scenario():
        async with queues(endpoint) as (clients, admin, namespace):
            queue, second = clients
            await queue.admit(
                [job("unsafe"), job("safe", max_attempts=2, retry_delay=0, retry_interrupted=True)]
            )
            claims = [await queue.claim("worker") for _ in range(2)]
            unsafe = next(claim for claim in claims if claim.job.id == "unsafe")
            safe = next(claim for claim in claims if claim.job.id == "safe")
            assert await second.renew(safe)
            for identifier in ("unsafe", "safe"):
                await move_clock_boundary(admin, namespace, identifier, "lease_until", 3)
            newer = await second.claim("replacement")
            assert newer.job.id == "safe" and newer.attempt == 2 and newer.token != safe.token
            assert not await queue.renew(safe)
            assert not await queue.finish(safe, "successful")
            assert not await queue.finish(unsafe, "successful")
            assert await second.finish(newer, "failed", retry=True)
            assert await queue.claim("exhausted") is None
            outcomes = {info.id: info for info in await queue.inspect()}
            assert outcomes["unsafe"].status == "interrupted"
            assert outcomes["unsafe"].error == "anyo.lease_expired"
            assert outcomes["safe"].status == "failed"
            assert outcomes["safe"].attempt == 2
            assert await queue.retry("unsafe")
            assert (await second.claim("manual")).attempt == 1

    run(scenario())


def test_delay_backoff_manual_retry_capacity_and_bounded_history(endpoint):
    async def scenario():
        settings = QueueSettings(capacity=1, max_records=2)
        async with queues(endpoint, settings=settings) as (clients, admin, namespace):
            queue = clients[0]
            await queue.admit([job("first", max_attempts=3, retry_delay=3000)])
            claim = await queue.claim("worker")
            assert await queue.finish(claim, "failed", retry=True)
            info = (await queue.inspect())[0]
            seconds, micros = await admin.time()
            assert seconds + micros / 1e6 + 2990 < info.available_at
            assert await queue.claim("not-yet") is None
            await move_clock_boundary(admin, namespace, "first", "available_at", 2)
            claim = await queue.claim("second-attempt")
            assert claim.attempt == 2
            assert await queue.finish(claim, "failed", retry=True)
            info = (await queue.inspect())[0]
            seconds, micros = await admin.time()
            assert 3590 < info.available_at - seconds - micros / 1e6 <= 3600
            await move_clock_boundary(admin, namespace, "first", "available_at", 2)
            claim = await queue.claim("last-attempt")
            assert await queue.finish(claim, "failed", retry=True)
            await queue.admit([job("second")])
            with pytest.raises(CapacityError):
                await queue.retry("first")
            claim = await queue.claim("worker")
            await queue.finish(claim, "successful")
            with pytest.raises(CapacityError):
                await queue.admit([job("third")])
            await move_clock_boundary(admin, namespace, "first", "completed_at", 4)
            assert await queue.admit([job("third")]) == 1
            assert {info.id for info in await queue.inspect()} == {"second", "third"}

    run(scenario())


def test_reconnect_after_socket_interruption_and_lost_ack_deduplicates(endpoint):
    async def scenario():
        async with queues(endpoint) as (clients, admin, _):
            queue, worker = clients
            client = queue._client

            class LostAcknowledgment:
                async def execute_command(self, *args):
                    await client.execute_command(*args)
                    raise TimeoutError("acknowledgment lost after commit")

                async def aclose(self):
                    await client.aclose()

            queue._client = LostAcknowledgment()
            with pytest.raises(QueueUnavailable):
                await queue.admit([job("committed-before-disconnect")])
            queue._client = client
            # Close this consumer's pool, not any unrelated Redis client.
            await queue.close()
            await queue.open({"hook": "fingerprint"})
            assert await queue.admit([job("committed-before-disconnect")]) == 0
            claim = await worker.claim("consumer")
            assert claim.job.id == "committed-before-disconnect"
            assert await queue.finish(claim, "successful")
            # Kill only the connection known to the queue instance, simulating
            # a network disconnect. A subsequent call reconnects automatically.
            connection_id = await queue._execute("CLIENT", "ID")
            await admin.execute_command("CLIENT", "KILL", "ID", connection_id)
            try:
                await queue.inspect()
            except QueueUnavailable:
                pass  # The interrupted operation can fail; the next one reconnects.
            assert (await queue.inspect())[0].status == "successful"

    run(scenario())


def test_prevalidation_avoids_partial_writes_on_wrong_redis_key_type(endpoint):
    async def scenario():
        async with queues(endpoint) as (clients, admin, namespace):
            queue = clients[0]
            await admin.set(keys(namespace)[2], "wrong-type")
            with pytest.raises(QueueMismatch):
                await queue.admit([job("first"), job("second")])
            assert await admin.hlen(keys(namespace)[1]) == 0
            await admin.delete(keys(namespace)[2])
            assert await queue.admit([job("first"), job("second")]) == 2
            assert len(await queue.inspect()) == 2

    run(scenario())


def test_acl_changes_cannot_leave_partially_admitted_ghost_jobs(endpoint):
    """The explicit test Redis must permit temporary ACL-user administration."""

    async def scenario():
        async with queues(endpoint, count=1) as (clients, admin, namespace):
            username, password = "anyo-test-" + uuid4().hex, uuid4().hex
            parsed = urlsplit(endpoint)
            address = parsed.netloc.rsplit("@", 1)[-1]
            authenticated = urlunsplit(
                parsed._replace(
                    netloc=quote(username) + ":" + quote(password) + "@" + address,
                )
            )
            limited = RedisQueue(authenticated, namespace, QueueSettings())
            try:
                await admin.execute_command(
                    "ACL",
                    "SETUSER",
                    username,
                    "on",
                    ">" + password,
                    "~anyo:queue:*",
                    "+@all",
                    "-zadd",
                )
                with pytest.raises(QueueUnavailable, match="permissions"):
                    await limited.open({"hook": "fingerprint"})
                assert await admin.hlen(keys(namespace)[1]) == 0
                await admin.execute_command("ACL", "SETUSER", username, "+zadd")
                await limited.open({"hook": "fingerprint"})
                # Revocation after startup must also fail before HSET can write.
                await admin.execute_command("ACL", "SETUSER", username, "-zadd")
                with pytest.raises(QueueUnavailable, match="permissions"):
                    await limited.admit([job("first"), job("second")])
                assert await admin.hlen(keys(namespace)[1]) == 0
                assert await admin.zcard(keys(namespace)[2]) == 0
                version = (await admin.info("server"))["redis_version"]
                if int(version.split(".")[0]) >= 7:
                    # Per-command selectors require checking actual index keys;
                    # permission on the metadata key alone is insufficient.
                    selector = "(+zadd ~" + keys(namespace)[0] + ")"
                    await admin.execute_command("ACL", "SETUSER", username, selector)
                    with pytest.raises(QueueUnavailable, match="permissions"):
                        await limited.admit([job("first"), job("second")])
                    assert await admin.hlen(keys(namespace)[1]) == 0
                await admin.execute_command("ACL", "SETUSER", username, "+zadd")
                assert await limited.admit([job("first"), job("second")]) == 2
                assert (await limited.claim("worker")).job.id == "first"
                assert len(await clients[0].inspect()) == 2
            finally:
                await limited.close()
                await admin.execute_command("ACL", "DELUSER", username)

    run(scenario())


def test_retained_state_survives_real_server_restart(endpoint):
    """Additional opt-in: only restart an explicitly named disposable container."""
    container = os.environ.get("ANYO_TEST_REDIS_CONTAINER")
    if not container:
        pytest.skip("Set ANYO_TEST_REDIS_CONTAINER for isolated container restart verification")
    if not container.startswith("anyo-worker-redis-check"):
        pytest.fail(
            "Restart verification requires an anyo-worker-redis-check* disposable container"
        )
    docker = os.environ.get("ANYO_TEST_DOCKER", "/usr/local/bin/docker")

    async def scenario():
        async with queues(endpoint) as (clients, admin, _):
            queue, worker = clients
            persistence = {}
            for setting in ("appendonly", "appendfsync", "maxmemory-policy"):
                persistence.update(await admin.config_get(setting))
            assert persistence == {
                "appendonly": "yes",
                "appendfsync": "everysec",
                "maxmemory-policy": "noeviction",
            }
            await queue.admit([job("queued"), job("running")])
            claim = await worker.claim("before-restart")
            await asyncio.to_thread(
                subprocess.run,
                [docker, "stop", "--time", "5", container],
                check=True,
                capture_output=True,
                timeout=15,
            )
            try:
                with pytest.raises(QueueUnavailable):
                    await queue.inspect()
            finally:
                await asyncio.to_thread(
                    subprocess.run,
                    [docker, "start", container],
                    check=True,
                    capture_output=True,
                    timeout=15,
                )
            # Redis becomes reachable before/at successful Docker exec. Bounded
            # health retries are readiness polling, not test-order sleeps.
            for _ in range(100):
                try:
                    records = await queue.inspect()
                    break
                except QueueUnavailable:
                    await asyncio.sleep(0.02)
            else:
                pytest.fail("Redis did not recover after restart")
            assert {info.id for info in records} == {"queued", "running"}
            await admin.connection_pool.disconnect()
            try:
                await worker.renew(claim)
            except QueueUnavailable:
                pass  # Discover and discard the connection from before restart.
            assert await worker.finish(claim, "successful")
            remaining = await worker.claim("after-restart")
            assert remaining.job.id != claim.job.id
            assert await worker.finish(remaining, "successful")

    run(scenario())
