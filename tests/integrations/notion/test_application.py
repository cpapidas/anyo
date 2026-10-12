"""Authentication, admission, response budgets, privacy, and replay end to end."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient

from anyo.application import Application
from anyo.execution import QueueUnavailable
from anyo.hooks import RetryPolicy
from anyo.integrations.notion import NotionEvent, NotionProvider
from anyo.integrations.sqlite import SqliteQueue

from .conftest import SECRET, add, headers, payload, post


@pytest.mark.parametrize("queue", ["memory://", None])
def test_sync_async_private_fanout_and_concurrent_duplicates(app, queue):
    first, second = [], []

    def mutate(event):
        first.append(event)
        event.payload["workspace_name"] = "changed"

    async def callback(event):
        second.append(event)

    add(app, mutate)
    add(app, callback, hook_id="second")
    kwargs = {"queue_url": queue} if queue else {}
    with TestClient(app.asgi(**kwargs)) as client:
        with ThreadPoolExecutor(4) as executor:
            statuses = list(executor.map(lambda _: post(client).status_code, range(4)))
        assert statuses == [200] * 4
        if queue is None:
            client.portal.call(client.app.state.dispatcher.wait_idle)
    assert len(first) == len(second) == 1
    assert type(first[0]) is type(second[0]) is NotionEvent
    assert second[0].payload["workspace_name"] == "Private workspace name"


def test_per_hook_auth_and_repeated_signature_headers(app):
    one, two = [], []
    add(app, one.append)
    add(app, two.append, hook_id="two", secret="different")
    with TestClient(app.asgi()) as client:
        assert post(client).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
        assert len(one) == 1 and two == []
        assert post(client, secret="different").status_code == 200
        body = json.dumps(payload()).encode()
        signed = headers(body)
        repeated = list(signed.items()) + [("X-Notion-Signature", signed["x-notion-signature"])]
        assert client.post("/webhooks/notion", content=body, headers=repeated).status_code == 401
        assert post(client, secret="wrong").status_code == 401
        client.portal.call(client.app.state.dispatcher.wait_idle)
    assert len(one) == len(two) == 1


def test_unmatched_malformed_oversized_and_bootstrap_never_dispatch(app):
    add(app, lambda event: None, event_type="page.deleted")
    with TestClient(app.asgi(max_body_bytes=2048)) as client:
        assert post(client).status_code == 200
        assert post(client, {}).status_code == 400
        body = b"x" * 2049
        assert (
            client.post("/webhooks/notion", content=body, headers=headers(body)).status_code == 413
        )
        assert (
            client.post("/webhooks/notion", json={"verification_token": SECRET}).status_code == 401
        )
        assert client.get("/webhooks/notion").status_code == 405
        assert client.portal.call(client.app.state.dispatcher.inspect) == []


def test_atomic_capacity_and_retry_metadata_identity(app):
    add(app, lambda event: None)
    add(app, lambda event: None, hook_id="second")
    with TestClient(app.asgi(role="receiver", capacity=1)) as client:
        assert post(client).status_code == 503
        assert client.portal.call(client.app.state.dispatcher.inspect) == []
    with TestClient(app.asgi(role="receiver", capacity=2, queue_namespace="two")) as client:
        assert post(client).status_code == 200
        assert post(client, payload(attempt_number=8)).status_code == 200
        assert post(client, payload(id="other")).status_code == 503
        assert len(client.portal.call(client.app.state.dispatcher.inspect)) == 2


def test_commit_precedes_ack_and_ambiguous_commit_is_recoverable(tmp_path):
    class Uncertain(SqliteQueue):
        first = True

        async def admit(self, jobs):
            result = await super().admit(jobs)
            if self.first:
                self.first = False
                raise QueueUnavailable("private details")
            return result

    app = Application(
        providers=[NotionProvider()],
        queue_backends={"sqlite": Uncertain},
        queue_url=f"sqlite:///{tmp_path / 'queue.db'}",
    )
    add(app, lambda event: None)
    with TestClient(app.asgi(role="receiver")) as client:
        response = post(client)
        assert response.status_code == 503 and "private" not in response.text
        assert post(client).status_code == 200
        assert len(client.portal.call(client.app.state.dispatcher.inspect)) == 1


def test_queue_wait_is_bounded(tmp_path):
    class Waiting(SqliteQueue):
        async def admit(self, jobs):
            await asyncio.Event().wait()

    app = Application(
        providers=[NotionProvider(request_timeout=0.05)],
        queue_backends={"sqlite": Waiting},
        queue_url=f"sqlite:///{tmp_path / 'queue.db'}",
    )
    add(app, lambda event: None)
    with TestClient(app.asgi(role="receiver")) as client:
        assert post(client).status_code == 503
        assert client.portal.call(client.app.state.dispatcher.inspect) == []


def test_slow_callback_does_not_delay_ack(app):
    entered, release = threading.Event(), threading.Event()

    def slow(event):
        entered.set()
        assert release.wait(5)

    add(app, slow)
    with TestClient(app.asgi()) as client:
        try:
            assert post(client).status_code == 200
            assert entered.wait(5) and not release.is_set()
            assert post(client).status_code == 200
        finally:
            release.set()
        client.portal.call(client.app.state.dispatcher.wait_idle)


def test_restart_and_secret_free_worker(app, monkeypatch):
    calls = []
    add(app, calls.append, secret=None)
    monkeypatch.delenv("ANYO_NOTION_VERIFICATION_TOKEN", raising=False)
    with pytest.raises(ValueError, match="ANYO_NOTION_VERIFICATION_TOKEN"):
        with TestClient(app.asgi()):
            pass
    monkeypatch.setenv("ANYO_NOTION_VERIFICATION_TOKEN", SECRET)
    with TestClient(app.asgi(role="receiver")) as client:
        assert post(client, payload("page.deleted")).status_code == 200
    monkeypatch.delenv("ANYO_NOTION_VERIFICATION_TOKEN")

    async def work():
        async with app.worker() as runner:
            await asyncio.wait_for(runner.wait_idle(), 5)

    asyncio.run(work())
    assert len(calls) == 1 and calls[0].event_type == "page.deleted"
    monkeypatch.setenv("ANYO_NOTION_VERIFICATION_TOKEN", SECRET)
    with TestClient(app.asgi()) as client:
        assert post(client, payload("page.deleted", attempt_number=2)).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
    assert len(calls) == 1


def test_failed_sibling_replay_preserves_admitted_event(app, caplog):
    calls, good = [], []

    def fail(event):
        calls.append(event)
        if len(calls) < 3:
            raise RuntimeError("Private workspace name " + SECRET)

    add(app, fail, retry=RetryPolicy(2, delay=0))
    add(app, good.append, hook_id="good")
    with TestClient(app.asgi(operations=True)) as client:
        assert post(client).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
        assert len(calls) == 2
        assert post(client, payload(attempt_number=8)).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
    assert len(good) == 1 and len(calls) == 3
    assert all(event.attempt_number == 1 for event in calls)
    assert SECRET not in caplog.text and "Private workspace name" not in caplog.text


def test_immediate_responders_rejected_at_registration(app):
    async def responder(event):
        raise AssertionError("Must never run")

    with pytest.raises(ValueError, match="does not support immediate responders"):
        add(app, None, responder=responder)


def test_history_expiry_allows_new_execution_without_expiring_active_work(tmp_path):
    class ClockedQueue(SqliteQueue):
        now = 1000.0

        @staticmethod
        def _now(connection):
            return ClockedQueue.now

    calls = []
    app = Application(
        providers=[NotionProvider()],
        queue_backends={"sqlite": ClockedQueue},
        queue_url=f"sqlite:///{tmp_path / 'expiry.db'}",
    )
    add(app, calls.append)
    with TestClient(app.asgi(retention_seconds=10)) as client:
        assert post(client).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
        ClockedQueue.now = 1009
        assert post(client, payload(attempt_number=2)).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
        assert len(calls) == 1
        ClockedQueue.now = 1011
        assert post(client, payload(attempt_number=3)).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
        assert len(calls) == 2 and calls[-1].attempt_number == 3
    with TestClient(
        app.asgi(role="receiver", retention_seconds=10, queue_namespace="backlog")
    ) as client:
        assert post(client).status_code == 200
        ClockedQueue.now = 2000
        assert post(client, payload(attempt_number=8)).status_code == 200
        records = client.portal.call(client.app.state.dispatcher.inspect)
        assert len(records) == 1 and records[0].status == "queued"
