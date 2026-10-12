"""Public embedding, extension, and ownership contracts for consumer hosts."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import textwrap
from contextlib import AsyncExitStack, asynccontextmanager

import httpx2
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

import anyo
from anyo.integrations.github import GitHubProvider
from anyo.integrations.sqlite import SqliteQueue

SECRET = "hosting-test-secret"


def register(app, callback, *, repository="repo"):
    app.add_hook(
        provider="github",
        action="pr_created",
        organization="org",
        repository=repository,
        pipeline=callback,
        secret=SECRET,
        hook_id=repository,
    )


def signed(*, identity="one", repository="repo", secret=SECRET):
    body = json.dumps(
        {
            "action": "opened",
            "number": 1,
            "repository": {"name": repository, "owner": {"login": "org"}},
            "pull_request": {"head": {"sha": "a" * 40}},
        },
        indent=2,  # Authentication must preserve these exact bytes.
    ).encode()
    return {
        "content": body,
        "headers": {
            "x-github-event": "pull_request",
            "x-github-delivery": identity,
            "x-hub-signature-256": "sha256="
            + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
        },
    }


def mounted(*apps):
    @asynccontextmanager
    async def host_lifespan(host):
        async with AsyncExitStack() as stack:
            for _, app in apps:
                await stack.enter_async_context(anyo.lifespan(app))
            yield

    async def health(request):
        return JSONResponse({"host": "ready"})

    return Starlette(
        routes=[Route("/health", health), *(Mount(path, app=app) for path, app in apps)],
        lifespan=host_lifespan,
    )


def test_package_api_shares_the_default_registry_and_sqlite_defaults(tmp_path):
    # Use a new interpreter so the package-level registry stays isolated from
    # other tests. The script imports the installed package outside the checkout.
    script = textwrap.dedent(
        """\
        import anyo
        from pathlib import Path
        from starlette.testclient import TestClient

        anyo.add_hook(provider="github", action="pr_created", organization="org",
                      repository="repo", pipeline=lambda event: None,
                      hook_id="default", secret="test")
        with TestClient(anyo.asgi(role="receiver")) as client:
            assert client.post("/webhooks/github", content=b"{}").status_code == 401
            assert Path(".anyo/queue.sqlite3").exists()
            assert client.get("/ui/").status_code == 404
        """
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=environment, check=True, timeout=15
    )


def test_isolated_apps_mount_together_and_keep_delivery_history_separate(tmp_path):
    first, second = [], []
    a = anyo.create_application(queue_url=f"sqlite:///{tmp_path / 'one.sqlite3'}")
    b = anyo.create_application(queue_url=f"sqlite:///{tmp_path / 'two.sqlite3'}")
    register(a, first.append)
    register(b, second.append, repository="other")
    one, two = a.asgi(workers=1), b.asgi(workers=1)
    with TestClient(mounted(("/one", one), ("/two", two))) as client:
        assert client.get("/health").json() == {"host": "ready"}
        assert client.post("/one/webhooks/github", **signed()).json() == {"accepted": 1}
        assert client.post("/one/webhooks/github", **signed()).json() == {"accepted": 0}
        assert client.post("/two/webhooks/github", **signed()).json() == {"accepted": 0}
        assert client.post("/two/webhooks/github", **signed(repository="other")).json() == {
            "accepted": 1
        }
        client.portal.call(one.state.dispatcher.wait_idle)
        client.portal.call(two.state.dispatcher.wait_idle)
    assert [event.repository for event in first] == ["repo"]
    assert [event.repository for event in second] == ["other"]
    with TestClient(one) as client:
        assert client.post("/webhooks/github", **signed()).json() == {"accepted": 0}


def test_mount_without_lifespan_fails_closed_but_host_stays_available():
    app = anyo.create_application(queue_url="memory://")
    register(app, lambda event: None)
    http = app.asgi()
    # Do not enter the parent's lifecycle context.
    client = TestClient(mounted(("/anyo", http)))
    try:
        assert client.get("/health").status_code == 200
        assert client.post("/anyo/webhooks/github", **signed()).status_code == 503
    finally:
        client.close()


def test_public_lifespan_preserves_host_resources_and_rejects_concurrent_claims():
    async def run():
        calls = []
        resources = []

        @asynccontextmanager
        async def consumer(http):
            resources.append("open")
            try:
                yield
            finally:
                resources.append("close")

        app = anyo.create_application(queue_url="memory://")
        register(app, calls.append)
        http, other = app.asgi(lifespan=consumer), app.asgi(lifespan=consumer)
        loop = asyncio.get_running_loop()
        handlers = logging.getLogger().handlers[:]
        signals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        with socket.create_server(("127.0.0.1", 0)) as borrowed:
            for _ in range(2):
                async with anyo.lifespan(http):
                    with pytest.raises(RuntimeError, match="already running"):
                        async with anyo.lifespan(other):
                            pass
                    with pytest.raises(RuntimeError, match="running"):
                        register(app, calls.append, repository="late")
                    async with httpx2.AsyncClient(
                        transport=httpx2.ASGITransport(app=http), base_url="http://local"
                    ) as client:
                        assert (
                            await client.post("/webhooks/github", **signed())
                        ).status_code == 202
                assert borrowed.fileno() >= 0
                assert asyncio.get_running_loop() is loop and loop.is_running()
                assert logging.getLogger().handlers == handlers
                assert {s: signal.getsignal(s) for s in signals} == signals
        assert len(calls) == 2
        assert resources == ["open", "close"] * 2

    asyncio.run(run())


def test_custom_resources_outlive_cancelled_host_and_pipeline_drain():
    async def run():
        started, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        events = []
        resource = {"open": False}

        @asynccontextmanager
        async def resources(http):
            resource["open"] = True
            events.append("open")
            try:
                yield {"resource": resource}
            finally:
                resource["open"] = False
                events.append("close")
                closed.set()

        async def pipeline(event):
            assert resource["open"]
            started.set()
            await release.wait()
            assert resource["open"]
            events.append("pipeline finished")

        app = anyo.create_application(queue_url="memory://")
        register(app, pipeline)
        http = app.asgi(lifespan=resources)

        async def host():
            async with anyo.lifespan(http) as state:
                assert state["resource"] is resource
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=http), base_url="http://local"
                ) as client:
                    assert (await client.post("/webhooks/github", **signed())).status_code == 202
                await asyncio.Event().wait()

        before = asyncio.all_tasks()
        task = asyncio.create_task(host())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert closed.is_set() and events == ["open", "pipeline finished", "close"]
        assert not (asyncio.all_tasks() - before)
        async with anyo.lifespan(http):
            assert resource["open"]
        assert not resource["open"]

    asyncio.run(run())


def test_runtime_startup_failure_closes_consumer_and_backend_resources(tmp_path):
    opened, closed, resources = [], [], []
    fail = [True]

    class UnavailableQueue(SqliteQueue):
        async def open(self, registry):
            await super().open(registry)
            opened.append(self)
            if fail[0]:
                raise RuntimeError("controlled startup failure")

        async def close(self):
            await super().close()
            closed.append(self)

    @asynccontextmanager
    async def consumer(http):
        resources.append("opened")
        try:
            yield
        finally:
            resources.append("closed")

    app = anyo.Application(
        providers=[GitHubProvider()],
        queue_backends={"sqlite": UnavailableQueue},
        queue_url=f"sqlite:///{tmp_path / 'failed.sqlite3'}",
    )
    register(app, lambda event: None)
    http = app.asgi(lifespan=consumer)
    with pytest.raises(RuntimeError, match="controlled startup failure"):
        with TestClient(http):
            pass
    assert opened == closed and resources == ["opened", "closed"]
    fail[0] = False
    with TestClient(http) as client:
        assert client.post("/webhooks/github", **signed()).status_code == 202
        client.portal.call(http.state.dispatcher.wait_idle)
    assert opened == closed and resources == ["opened", "closed"] * 2


def test_host_startup_failure_unwinds_already_started_child():
    events = []

    @asynccontextmanager
    async def consumer(http):
        events.append("open")
        try:
            yield
        finally:
            events.append("close")

    instance = anyo.create_application(queue_url="memory://")
    register(instance, lambda event: None)
    child = instance.asgi(lifespan=consumer)

    @asynccontextmanager
    async def failing_host(host):
        async with anyo.lifespan(child):
            raise RuntimeError("host cannot start")
            yield

    with pytest.raises(RuntimeError, match="host cannot start"):
        with TestClient(Starlette(lifespan=failing_host)):
            pass
    assert events == ["open", "close"]
    with TestClient(child) as client:
        assert client.post("/webhooks/github", **signed()).status_code == 202
    assert events == ["open", "close"] * 2


def test_mounted_receiver_preserves_authentication_body_limits_and_capacity(tmp_path):
    app = anyo.create_application(queue_url=f"sqlite:///{tmp_path / 'queue.sqlite3'}")
    register(app, lambda event: None)
    child = app.asgi(role="receiver", capacity=1, max_body_bytes=512)
    with TestClient(mounted(("/anyo", child))) as client:
        path = "/anyo/webhooks/github"
        assert client.post(path, **signed(secret="wrong")).status_code == 401
        assert client.post(path, content=b"x" * 513).status_code == 413
        assert client.post(path, **signed()).status_code == 202
        assert client.post(path, **signed()).json() == {"accepted": 0}
        assert client.post(path, **signed(identity="two")).status_code == 503
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("options", [{"host": ""}, {"port": True}, {"port": -1}])
def test_serve_validates_server_options(options):
    with pytest.raises(ValueError):
        anyo.serve(Starlette(), **options)


def test_serve_rejects_event_loop_and_runtime_options():
    async def run():
        with pytest.raises(RuntimeError, match="event loop"):
            anyo.serve(Starlette())

    asyncio.run(run())
    with pytest.raises(TypeError):
        anyo.serve(Starlette(), queue_url="memory://")
