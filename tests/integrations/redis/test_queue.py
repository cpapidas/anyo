"""Redis adapter boundaries that do not need Redis installed or running."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from anyo.execution._contracts import QueueMismatch, QueueSettings, QueueUnavailable
from anyo.integrations.redis import RedisQueue


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "redis://",
        "redis://localhost/not-a-database",
        "redis://localhost:0/0",
        "redis://localhost:invalid/0",
        "redis://localhost/0?socket_timeout=9999",
        "redis://localhost/0#fragment",
    ],
)
def test_rejects_invalid_urls_without_connecting(url):
    with pytest.raises(ValueError):
        RedisQueue(url, "example", QueueSettings())


def test_missing_optional_client_has_installation_hint(monkeypatch):
    original = importlib.import_module

    def missing(name):
        if name == "redis.asyncio":
            raise ModuleNotFoundError("No module named redis", name="redis")
        return original(name)

    async def scenario():
        queue = RedisQueue("redis://localhost", "example", QueueSettings())
        monkeypatch.setattr(importlib, "import_module", missing)
        with pytest.raises(ImportError, match=r"anyo\[redis\]"):
            await queue.open({"hook": "fingerprint"})
        await queue.close()

    asyncio.run(scenario())


class TransportError(Exception):
    pass


class Client:
    def __init__(self, *, version="6.2.0"):
        self.version = version
        self.closed = False
        self.fail = False
        self.hold = None

    async def execute_command(self, *args):
        if self.hold is not None:
            await self.hold.wait()
        if self.fail:
            raise TransportError("connection lost")
        if args[0] == "INFO":
            return {"redis_version": self.version}
        return ["ok"]

    async def aclose(self):
        self.closed = True


def install_client(monkeypatch, client):
    original = importlib.import_module

    def load(name):
        if name == "redis.asyncio":
            return SimpleNamespace(Redis=SimpleNamespace(from_url=lambda *a, **kw: client))
        if name == "redis.exceptions":
            return SimpleNamespace(RedisError=TransportError)
        return original(name)

    monkeypatch.setattr(importlib, "import_module", load)


def test_server_minimum_checked_before_writes_and_client_closed(monkeypatch):
    client = Client(version="5.0.14")
    install_client(monkeypatch, client)

    async def scenario():
        queue = RedisQueue("redis://localhost", "example", QueueSettings())
        with pytest.raises(QueueMismatch, match="6.2"):
            await queue.open({})
        assert client.closed

    asyncio.run(scenario())


def test_unavailable_operations_are_bounded_and_can_reconnect(monkeypatch):
    client = Client()
    install_client(monkeypatch, client)

    async def scenario():
        queue = RedisQueue(
            "redis://user:secret@localhost",
            "example",
            QueueSettings(
                operation_timeout=0.01,
            ),
        )
        await queue.open({})
        client.fail = True
        with pytest.raises(QueueUnavailable) as failure:
            await queue.inspect()
        assert "secret" not in str(failure.value)
        client.fail = False
        client.hold = asyncio.Event()
        with pytest.raises(QueueUnavailable) as failure:
            await queue.inspect()
        assert isinstance(failure.value.__cause__, TimeoutError)
        client.hold.set()
        assert await queue.inspect() == []
        await queue.close()
        assert client.closed

    asyncio.run(scenario())


def test_cancellation_is_not_converted_to_storage_failure(monkeypatch):
    client = Client()
    install_client(monkeypatch, client)

    async def scenario():
        queue = RedisQueue("redis://localhost", "example", QueueSettings())
        await queue.open({})
        client.hold = asyncio.Event()
        operation = asyncio.create_task(queue.inspect())
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await queue.close()
        assert client.closed

    asyncio.run(scenario())


def test_concurrent_operations_wait_for_pool_slots_instead_of_failing(monkeypatch):
    class BoundedClient(Client):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.maximum = 0
            self.full = asyncio.Event()
            self.release = asyncio.Event()
            self.block = False

        async def execute_command(self, *args):
            if not self.block:
                return await super().execute_command(*args)
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            assert self.active <= 32, "pool capacity exceeded"
            if self.active == 32:
                self.full.set()
            try:
                await self.release.wait()
                return ["ok"]
            finally:
                self.active -= 1

    async def scenario():
        client = BoundedClient()
        install_client(monkeypatch, client)
        queue = RedisQueue("redis://localhost", "example", QueueSettings())
        await queue.open({})
        client.block = True
        pending = [asyncio.create_task(queue.inspect()) for _ in range(48)]
        await asyncio.wait_for(client.full.wait(), timeout=1)
        client.release.set()
        assert await asyncio.gather(*pending) == [[]] * 48
        assert client.maximum == 32
        await queue.close()

    asyncio.run(scenario())
