"""Application ownership for durable workers, prompt clients, and process shutdown."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import queue
import signal
import subprocess
import sys
import textwrap
import threading

import pytest
from starlette.testclient import TestClient

import anyo
from anyo.agents import Harness, Model, PromptResult, PromptSuccess
from anyo.application import Application
from anyo.execution import QueueUnavailable
from anyo.integrations.github import GitHubProvider
from anyo.integrations.sqlite import SqliteQueue

SECRET = "queue-application-test-secret"


def application(path, *, harness_factory=None):
    builders = {}
    if harness_factory is not None:
        builders["fake"] = lambda **kwargs: Harness(
            "fake", (Model("test/model", "key"),), (), harness_factory
        )
    app = Application(
        providers=[GitHubProvider()],
        harnesses=builders,
        queue_backends={"sqlite": SqliteQueue},
        queue_url=f"sqlite:///{path}",
    )
    if harness_factory is not None:
        app.harness(provider="fake", models=[])
    return app


def add(app, key, pipeline, *, secret=None):
    return app.add_hook(
        provider="github",
        action="pr_created",
        organization="org",
        repository="repo",
        pipeline=pipeline,
        secret=secret,
        hook_id=key,
    )


def deliver(http, delivery="one"):
    body = json.dumps(
        {
            "action": "opened",
            "number": 1,
            "repository": {"name": "repo", "owner": {"login": "org"}},
            "pull_request": {"head": {"sha": "a" * 40}},
        }
    ).encode()
    return http.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-github-event": "pull_request",
            "x-github-delivery": delivery,
            "x-hub-signature-256": "sha256="
            + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
        },
    )


class PromptBackend:
    def __init__(self, *, concurrent=1):
        self.requests = []
        self.closed = False
        self.concurrent = concurrent
        self.entered = asyncio.Event()
        self.loop = None

    async def invoke(self, request, emit):
        self.loop = asyncio.get_running_loop()
        self.requests.append(request)
        if len(self.requests) == self.concurrent:
            self.entered.set()
        await self.entered.wait()
        return PromptResult(request.run_id, PromptSuccess({"worked": True}))

    async def close(self):
        self.closed = True


def test_receiver_only_never_opens_prompt_client_then_worker_owns_sync_async_calls(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("ANYO_GITHUB_WEBHOOK_SECRET", raising=False)
    path = tmp_path / "queue.sqlite3"
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review")
    outcomes = []
    receiver_clients = []

    def receiver_factory():
        receiver_clients.append(True)
        raise AssertionError("receiver-only startup must not open prompt clients")

    def sync_pipeline(received):
        outcomes.append(anyo.inject_prompt(prompt, workspace=tmp_path))

    async def async_pipeline(received):
        outcomes.append(await anyo.ainject_prompt(prompt, workspace=tmp_path))

    receiver = application(path, harness_factory=receiver_factory)
    add(receiver, "sync", sync_pipeline, secret=SECRET)
    add(receiver, "async", async_pipeline, secret=SECRET)
    with TestClient(receiver.asgi(role="receiver")) as http:
        assert deliver(http).json() == {"accepted": 2}
    assert not receiver_clients and not outcomes

    async def exercise():
        backend = PromptBackend(concurrent=2)
        factories = []

        def worker_factory():
            factories.append(True)
            return backend

        worker = application(path, harness_factory=worker_factory)
        add(worker, "sync", sync_pipeline)
        add(worker, "async", async_pipeline)
        async with worker.worker(workers=2, poll_interval=0.01) as runner:
            with pytest.raises(RuntimeError):
                worker.reset_harness()
            await asyncio.wait_for(runner.wait_idle(), 5)
            assert backend.loop is asyncio.get_running_loop()
            assert not backend.closed
            assert all(record.status == "successful" for record in await runner.inspect())
        assert backend.closed and len(factories) == 1
        worker.reset_harness()
        assert len(backend.requests) == 2
        assert len(outcomes) == 2 and all(
            result.success.data == {"worked": True} for result in outcomes
        )
        with pytest.raises(RuntimeError, match="PromptClient"):
            await anyo.ainject_prompt(prompt)

    asyncio.run(exercise())


@pytest.mark.parametrize("role", ["worker", "all"])
def test_storage_startup_failure_releases_registration_and_owned_prompt_client(tmp_path, role):
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied")
    backend = PromptBackend()
    app = application(parent / "queue.sqlite3", harness_factory=lambda: backend)
    add(app, "pipeline", lambda event: None, secret=SECRET)

    async def exercise():
        async with app.worker():
            raise AssertionError("broken storage must not start")

    with pytest.raises(QueueUnavailable):
        if role == "worker":
            asyncio.run(exercise())
        else:
            with TestClient(app.asgi()):
                raise AssertionError("broken storage must not start")
    assert backend.closed
    app.reset_harness()
    add(app, "after-failure", lambda event: None, secret=SECRET)


def test_worker_harness_factory_failure_releases_the_application(tmp_path):
    def failing_factory():
        raise RuntimeError("fixture startup failure")

    app = application(tmp_path / "queue.sqlite3", harness_factory=failing_factory)
    add(app, "pipeline", lambda event: None)

    async def exercise():
        with pytest.raises(RuntimeError, match="fixture startup failure"):
            async with app.worker():
                raise AssertionError("broken harness must not start")
        app.reset_harness()
        async with app.worker() as runner:
            await runner.wait_idle()

    asyncio.run(exercise())


def test_worker_failure_propagates_after_prompt_cleanup_and_allows_reuse(tmp_path):
    backend = PromptBackend()
    app = application(tmp_path / "queue.sqlite3", harness_factory=lambda: backend)

    async def callback(received):
        raise asyncio.CancelledError

    registered = add(app, "cancelled", callback)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            async with app.worker() as runner:
                from anyo.hooks import HookEvent

                await runner.submit([(registered, HookEvent("github", "pr_created", "one"))])
                await runner.wait_failure()
        assert backend.closed
        app.reset_harness()
        async with app.worker() as runner:
            assert [record.status for record in await runner.inspect()] == ["interrupted"]

    asyncio.run(exercise())


@pytest.mark.skipif(os.name != "posix", reason="Exercises POSIX signal handler restoration")
def test_worker_startup_failure_restores_host_signal_handlers():
    original = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def handler(signum, frame):
        pass

    try:
        for sig in original:
            signal.signal(sig, handler)
        with pytest.raises(ValueError, match="Register at least"):
            Application().start(role="worker", queue_url="sqlite:///:memory:")
        assert all(signal.getsignal(sig) is handler for sig in original)
    finally:
        for sig, previous in original.items():
            signal.signal(sig, previous)


def test_worker_system_exit_cleans_resources_before_propagating(tmp_path):
    backend = PromptBackend()
    app = application(tmp_path / "queue.db", harness_factory=lambda: backend)

    def callback(received):
        raise SystemExit(7)

    add(app, "fatal", callback, secret=SECRET)
    with TestClient(app.asgi(role="receiver")) as http:
        assert deliver(http).status_code == 202
    with pytest.raises(SystemExit) as error:
        app.start(role="worker")
    assert error.value.code == 7
    assert backend.closed
    app.reset_harness()

    async def inspect():
        async with app.worker() as runner:
            assert [item.status for item in await runner.inspect()] == ["interrupted"]

    asyncio.run(inspect())


@pytest.mark.skipif(os.name != "posix", reason="Exercises POSIX worker SIGTERM handling")
def test_worker_process_sigterm_finishes_owned_job_and_next_process_resumes_backlog(tmp_path):
    path = tmp_path / "queue.sqlite3"
    receiver = application(path)
    add(receiver, "worker-job", lambda event: None, secret=SECRET)
    with TestClient(receiver.asgi(role="receiver")) as http:
        assert deliver(http, "one").status_code == 202
        assert deliver(http, "two").status_code == 202

    script = tmp_path / "worker.py"
    script.write_text(
        textwrap.dedent("""\
        import json
        import logging
        import socket
        import sys
        import threading
        from pathlib import Path
        from anyo.application import Application
        from anyo.integrations.github import GitHubProvider
        from anyo.integrations.sqlite import SqliteQueue

        logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
        release = threading.Event()

        def forbidden_listener(*args, **kwargs):
            raise AssertionError("worker role must not create a listener")

        socket.create_server = forbidden_listener

        def control():
            for line in sys.stdin:
                if line.strip() == "release":
                    release.set()

        def pipeline(event):
            print("STARTED:" + event.delivery_id, flush=True)
            if not release.wait(10):
                raise TimeoutError("Fixture was not released")
            output = Path("results.json")
            values = json.loads(output.read_text()) if output.exists() else []
            values.append(event.delivery_id)
            output.write_text(json.dumps(values))
            print("FINISHED:" + event.delivery_id, flush=True)

        app = Application(
            providers=[GitHubProvider()], queue_backends={"sqlite": SqliteQueue},
            queue_url="sqlite:///queue.sqlite3",
        )
        app.add_hook(
            provider="github", action="pr_created", organization="org", repository="repo",
            pipeline=pipeline, hook_id="worker-job",
        )
        threading.Thread(target=control, daemon=True).start()
        app.start(role="worker", workers=1, poll_interval=0.01)
        print("WORKER_CLOSED", flush=True)
    """)
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("ANYO_GITHUB_WEBHOOK_SECRET", None)

    def finish_one(delivery):
        process = subprocess.Popen(
            [sys.executable, "-u", str(script)],
            cwd=tmp_path,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines = queue.Queue()
        transcript = []

        def read():
            for line in process.stdout:
                lines.put(line.rstrip())
            lines.put(None)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()

        def expect(expected):
            while True:
                try:
                    line = lines.get(timeout=10)
                except queue.Empty:
                    pytest.fail(f"Timed out expecting {expected}: {transcript}")
                if line is None:
                    pytest.fail(f"Process exited expecting {expected}: {transcript}")
                transcript.append(line)
                if line == expected:
                    return

        try:
            expect(f"STARTED:{delivery}")
            process.send_signal(signal.SIGTERM)
            expect("Anyo worker shutdown requested")
            assert process.poll() is None
            process.stdin.write("release\n")
            process.stdin.flush()
            expect(f"FINISHED:{delivery}")
            expect("WORKER_CLOSED")
            assert process.wait(timeout=10) == 0, transcript
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            process.stdin.close()
            reader.join(timeout=10)
            assert not reader.is_alive()
            process.stdout.close()

    finish_one("one")
    assert json.loads((tmp_path / "results.json").read_text()) == ["one"]
    asgi = receiver.asgi(role="receiver")
    with TestClient(asgi) as http:
        records = http.portal.call(asgi.state.dispatcher.inspect)
        assert sorted(record.status for record in records) == ["queued", "successful"]
    finish_one("two")
    assert json.loads((tmp_path / "results.json").read_text()) == ["one", "two"]
