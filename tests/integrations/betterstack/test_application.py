"""Real ASGI admission and queue semantics for Better Stack notifications."""

import asyncio
import json
import threading

import pytest
from starlette.testclient import TestClient

from anyo.application import Application
from anyo.execution import QueueUnavailable
from anyo.hooks import RetryPolicy
from anyo.integrations.betterstack import BetterStackEvent, BetterStackProvider
from anyo.integrations.sqlite import SqliteQueue

from .conftest import HEADERS, SECRET, add, error, incident, post


@pytest.mark.parametrize("queue", ["memory://", None])
def test_both_families_private_fanout_and_duplicates(app, queue):
    first, second, errors = [], [], []
    add(app, first.append)

    async def callback(event):
        second.append(event)

    add(app, callback, hook_id="second")
    add(app, errors.append, action="error", hook_id="errors", application_id=4821)
    kwargs = {"queue_url": queue} if queue else {}
    with TestClient(app.asgi(**kwargs)) as client:
        for payload in (incident(), error()):
            for _ in range(2):
                result = post(client, payload)
                assert result.status_code == 200 and result.content == b""
        if queue is None:
            client.portal.call(client.app.state.dispatcher.wait_idle)
    assert len(first) == len(second) == len(errors) == 1
    assert all(isinstance(e, BetterStackEvent) for e in (*first, *second, *errors))
    assert first[0].payload is not second[0].payload


def test_each_secret_only_authorizes_its_hooks(app):
    first, second = [], []
    add(app, first.append)
    add(app, second.append, hook_id="other", secret="different-secret")
    with TestClient(app.asgi()) as client:
        assert post(client).status_code == 200
        assert post(client, headers={**HEADERS, "x-anyo-webhook-secret": "bad"}).status_code == 401
        assert (
            post(
                client, headers={**HEADERS, "x-anyo-webhook-secret": "different-secret"}
            ).status_code
            == 200
        )
        client.portal.call(client.app.state.dispatcher.wait_idle)
    assert len(first) == len(second) == 1


@pytest.mark.parametrize("values", [[SECRET, SECRET], [SECRET, "wrong"], ["wrong", SECRET]])
def test_repeated_credentials_rejected_in_http(app, values):
    calls = []
    add(app, calls.append)
    headers = [("Content-Type", "application/json")]
    headers += [("X-Anyo-Webhook-Secret", value) for value in values]
    with TestClient(app.asgi()) as client:
        response = post(client, headers=headers)
        assert response.status_code == 401
        assert client.portal.call(client.app.state.dispatcher.inspect) == []
    assert calls == []


def test_ignored_and_invalid_input_have_no_jobs(app):
    add(app, lambda event: None, event_type="resolved", monitor_id="9")
    with TestClient(app.asgi(max_body_bytes=1024)) as client:
        assert post(client).status_code == 200
        assert post(client, {"data": {"type": "release"}}).status_code == 200
        assert post(client, {}).status_code == 400
        assert (
            client.post("/webhooks/betterstack", content=b"x" * 1025, headers=HEADERS).status_code
            == 413
        )
        assert client.get("/webhooks/betterstack").status_code == 405
        assert client.portal.call(client.app.state.dispatcher.inspect) == []


def test_capacity_is_atomic_and_duplicates_succeed_when_full(app):
    add(app, lambda event: None)
    add(app, lambda event: None, hook_id="second")
    with TestClient(app.asgi(role="receiver", capacity=1)) as client:
        assert post(client).status_code == 503
        assert client.portal.call(client.app.state.dispatcher.inspect) == []
    with TestClient(app.asgi(role="receiver", capacity=2, queue_namespace="two")) as client:
        assert post(client).status_code == 200
        assert post(client).status_code == 200
        assert post(client, incident("resolved")).status_code == 503
        assert len(client.portal.call(client.app.state.dispatcher.inspect)) == 2


def test_queue_commit_precedes_ack_and_uncertain_result_deduplicates(tmp_path):
    class Uncertain(SqliteQueue):
        first = True

        async def admit(self, jobs):
            result = await super().admit(jobs)
            if self.first:
                self.first = False
                raise QueueUnavailable("private storage details")
            return result

    app = Application(
        providers=[BetterStackProvider()],
        queue_backends={"sqlite": Uncertain},
        queue_url=f"sqlite:///{tmp_path / 'queue.db'}",
    )
    add(app, lambda event: None)
    with TestClient(app.asgi(role="receiver")) as client:
        response = post(client)
        assert response.status_code == 503 and "private" not in response.text
        assert post(client).status_code == 200
        assert len(client.portal.call(client.app.state.dispatcher.inspect)) == 1


def test_queue_wait_is_bounded_by_total_deadline(tmp_path):
    class Waiting(SqliteQueue):
        async def admit(self, jobs):
            await asyncio.Event().wait()

    app = Application(
        providers=[BetterStackProvider(request_timeout=0.05)],
        queue_backends={"sqlite": Waiting},
        queue_url=f"sqlite:///{tmp_path / 'queue.db'}",
    )
    add(app, lambda event: None)
    with TestClient(app.asgi(role="receiver")) as client:
        assert post(client).status_code == 503
        assert client.portal.call(client.app.state.dispatcher.inspect) == []


def test_slow_pipeline_does_not_delay_ack(app):
    started, release = threading.Event(), threading.Event()

    def slow(event):
        started.set()
        assert release.wait(5)

    add(app, slow)
    with TestClient(app.asgi()) as client:
        try:
            assert post(client).status_code == 200
            assert started.wait(5)
            assert not release.is_set()
            assert post(client).status_code == 200
        finally:
            release.set()
        client.portal.call(client.app.state.dispatcher.wait_idle)


def test_receiver_restart_worker_without_secret_and_typed_transport(app, monkeypatch):
    calls = []
    add(app, calls.append, secret=None, action="error", hook_id="errors")
    monkeypatch.setenv("ANYO_BETTERSTACK_WEBHOOK_SECRET", SECRET)
    with TestClient(app.asgi(role="receiver")) as client:
        assert post(client, error()).status_code == 200
    assert calls == []
    monkeypatch.delenv("ANYO_BETTERSTACK_WEBHOOK_SECRET")

    async def run_worker():
        async with app.worker() as runner:
            await asyncio.wait_for(runner.wait_idle(), 5)
            assert [r.status for r in await runner.inspect()] == ["successful"]

    asyncio.run(run_worker())
    assert len(calls) == 1 and type(calls[0]) is BetterStackEvent
    assert calls[0].application_id == "4821"
    monkeypatch.setenv("ANYO_BETTERSTACK_WEBHOOK_SECRET", SECRET)
    with TestClient(app.asgi(role="receiver")) as client:
        assert post(client, error()).status_code == 200
        assert len(client.portal.call(client.app.state.dispatcher.inspect)) == 1


def test_retry_policy_and_redelivery_do_not_repeat_successful_siblings(app):
    calls, good = [], []

    def retry(event):
        calls.append(event)
        if len(calls) < 3:
            raise RuntimeError("private error")

    add(app, retry, retry=RetryPolicy(2, delay=0))
    add(app, good.append, hook_id="good")
    with TestClient(app.asgi()) as client:
        assert post(client).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
        assert len(calls) == 2
        assert post(client).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
        assert {r.status for r in client.portal.call(client.app.state.dispatcher.inspect)} == {
            "successful"
        }
    assert len(good) == 1 and len(calls) == 3


def test_error_privacy_in_provider_logs_and_stored_failures(app, caplog):
    def fail(event):
        raise RuntimeError("Private application message " + SECRET)

    add(app, fail, action="error")
    with TestClient(app.asgi(operations=True)) as client:
        assert post(client, error()).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
        records = client.portal.call(client.app.state.dispatcher.inspect)
        assert records[0].error == "RuntimeError"
    assert "Private application message" not in caplog.text
    assert SECRET not in caplog.text


def test_invalid_json_is_not_stored(app):
    add(app, lambda event: None)
    body = json.dumps(error()).replace('"new_error"', '"new_error", "event": "resolved"')
    with TestClient(app.asgi()) as client:
        assert (
            client.post("/webhooks/betterstack", content=body, headers=HEADERS).status_code == 400
        )
        assert client.portal.call(client.app.state.dispatcher.inspect) == []
