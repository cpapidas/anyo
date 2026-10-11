"""Durable webhook admission and event transport through the consumer API."""

import asyncio
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass

import pytest
from starlette.testclient import TestClient

from anyo.application import Application
from anyo.execution import QueueMismatch, QueueUnavailable
from anyo.execution._codec import EventCodecRegistry
from anyo.execution._identity import registry
from anyo.hooks import HookEvent, HookResponse, RetryPolicy
from anyo.integrations.github import GitHubProvider
from anyo.integrations.slack import SlackAction, SlackEvent, SlackInstallation, SlackProvider
from anyo.integrations.slack._codec import SlackEventCodec
from anyo.integrations.sqlite import SqliteQueue

SECRET = "test-durable-secret"


def application(tmp_path, *, providers=None, backends=None, codecs=()):
    return Application(
        providers=providers or [GitHubProvider()],
        queue_backends=backends or {"sqlite": SqliteQueue},
        queue_url=f"sqlite:///{tmp_path / 'queue.db'}",
        event_codecs=codecs,
    )


def add(app, pipeline, *, hook_id="review", **kwargs):
    return app.add_hook(
        provider="github",
        action="pr_created",
        organization="org",
        repository="repo",
        pipeline=pipeline,
        secret=SECRET,
        hook_id=hook_id,
        **kwargs,
    )


def post(client, identity="one"):
    body = json.dumps(
        {
            "action": "opened",
            "number": 7,
            "repository": {"name": "repo", "owner": {"login": "org"}},
            "pull_request": {"head": {"sha": "a" * 40}},
        }
    ).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-github-event": "pull_request",
            "x-github-delivery": identity,
            "x-hub-signature-256": "sha256="
            + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
        },
    )


def test_receiver_restart_and_reordered_worker_registry(tmp_path):
    calls = []
    receiver = application(tmp_path)
    add(receiver, lambda event: calls.append(("first", event)), hook_id="first")
    add(receiver, lambda event: calls.append(("second", event)), hook_id="second")
    with TestClient(receiver.asgi(role="receiver")) as client:
        assert post(client).json() == {"accepted": 2}
        assert post(client).json() == {"accepted": 0}
    assert not calls

    worker = application(tmp_path)
    add(worker, lambda event: calls.append(("second", event)), hook_id="second")
    add(worker, lambda event: calls.append(("first", event)), hook_id="first")

    async def consume():
        async with worker.worker() as runner:
            await asyncio.wait_for(runner.wait_idle(), 5)
            records = await runner.inspect()
            assert {record.status for record in records} == {"successful"}

    asyncio.run(consume())
    assert {label for label, _ in calls} == {"first", "second"}
    assert calls[0][1].payload is not calls[1][1].payload
    assert calls[0][1].pull_request_number == 7
    with TestClient(receiver.asgi(role="receiver")) as client:
        assert post(client).json() == {"accepted": 0}


def test_partial_success_explicit_redelivery_and_retry_policy(tmp_path):
    calls = []
    app = application(tmp_path)

    def flaky(event):
        calls.append("flaky")
        if calls.count("flaky") == 1:
            raise ValueError("private details must not be stored")

    add(app, lambda event: calls.append("good"), hook_id="good")
    add(app, flaky, hook_id="flaky")
    with TestClient(app.asgi()) as client:
        assert post(client).json() == {"accepted": 2}
        client.portal.call(client.app.state.dispatcher.wait_idle)
        records = client.portal.call(client.app.state.dispatcher.inspect)
        assert {item.status for item in records} == {"failed", "successful"}
        assert {item.error for item in records} == {None, "ValueError"}
        assert post(client).json() == {"accepted": 1}
        client.portal.call(client.app.state.dispatcher.wait_idle)
    assert calls.count("good") == 1
    assert calls.count("flaky") == 2


def test_retry_and_inspection_are_available_from_worker_context(tmp_path):
    app = application(tmp_path)
    attempts = []

    def flaky(event):
        attempts.append(event.delivery_id)
        if len(attempts) < 2:
            raise RuntimeError

    add(app, flaky, retry=RetryPolicy(max_attempts=2, delay=0))
    with TestClient(app.asgi()) as client:
        post(client)
        client.portal.call(client.app.state.dispatcher.wait_idle)
        [record] = client.portal.call(client.app.state.dispatcher.inspect)
        assert record.status == "successful"
        assert record.attempt == 2
        assert client.portal.call(client.app.state.dispatcher.retry, record.id) is False
    assert attempts == ["one", "one"]


def test_namespace_mismatch_leaves_pending_jobs_and_releases_application(tmp_path):
    first = application(tmp_path)
    add(first, lambda event: None)
    with TestClient(first.asgi(role="receiver")) as client:
        assert post(client).status_code == 202
    changed = application(tmp_path)
    add(changed, lambda event: None, hook_version="2")
    with pytest.raises(QueueMismatch):
        with TestClient(changed.asgi()):
            pass
    with TestClient(changed.asgi(queue_namespace="v2", role="receiver")) as client:
        assert post(client).status_code == 202
    with TestClient(first.asgi(role="receiver")) as client:
        [record] = client.portal.call(client.app.state.dispatcher.inspect)
        assert record.status == "queued"


def test_durable_identity_rejects_ambiguous_callable_but_memory_still_supports_it(tmp_path):
    app = application(tmp_path)
    add(app, lambda event: None, hook_id=None)
    with pytest.raises(ValueError, match="hook_id"):
        with TestClient(app.asgi()):
            pass
    with TestClient(app.asgi(queue_url="memory://")) as client:
        assert post(client).status_code == 202


def test_duplicate_explicit_hook_ids_are_rejected_before_startup(tmp_path):
    app = application(tmp_path)
    add(app, lambda event: None)
    with pytest.raises(ValueError, match="hook_id"):
        add(app, lambda event: None)


def top_level_one(event):
    pass


def top_level_two(event):
    pass


def test_automatic_identity_is_stable_across_registration_order(tmp_path):
    first, second = application(tmp_path), application(tmp_path)
    a = [add(first, top_level_one, hook_id=None), add(first, top_level_two, hook_id=None)]
    b = [add(second, top_level_two, hook_id=None), add(second, top_level_one, hook_id=None)]
    assert registry(a) == registry(b)


def test_slack_typed_context_round_trips_without_executable_serialization():
    codecs = EventCodecRegistry([SlackEventCodec()])
    event = SlackEvent(
        provider="slack",
        action="interaction",
        delivery_id="one",
        payload={"nested": [1, None]},
        event_type="block_actions",
        team_id="T1",
        actions=(SlackAction("approve", "review"),),
        installations=(SlackInstallation("T1", "E1"),),
        options_request=False,
    )
    copy = codecs.decode(codecs.encode(event))
    assert copy == event
    assert type(copy) is SlackEvent
    assert type(copy.actions[0]) is SlackAction
    assert type(copy.installations[0]) is SlackInstallation
    assert copy.payload is not event.payload


@dataclass(frozen=True)
class CustomEvent(HookEvent):
    item: str = ""


class CustomCodec:
    name = "test.custom.v1"
    event_type = CustomEvent

    def encode(self, event):
        return asdict(event)

    def decode(self, data):
        return CustomEvent(**data)


def test_custom_codec_must_be_explicit_and_schema_is_checked():
    event = CustomEvent("custom", "event", "one", item="value")
    with pytest.raises(ValueError, match="codec"):
        EventCodecRegistry().encode(event)
    codecs = EventCodecRegistry([CustomCodec()])
    encoded = codecs.encode(event)
    assert codecs.decode(encoded) == event
    data = json.loads(encoded)
    data["schema"] = 2
    with pytest.raises(ValueError, match="schema"):
        codecs.decode(json.dumps(data))
    data["schema"] = 1
    data["codec"] = "some.importable.module.Class"
    with pytest.raises(ValueError, match="codec"):
        codecs.decode(json.dumps(data))


@pytest.mark.parametrize("payload", [{"x": object()}, {"x": float("nan")}, {"x": (1, 2)}])
def test_non_json_events_are_rejected(payload):
    with pytest.raises(ValueError, match="JSON"):
        EventCodecRegistry().encode(HookEvent("github", "pr_created", "one", payload=payload))


def slack_post(client):
    body = json.dumps(
        {
            "type": "event_callback",
            "api_app_id": "A1",
            "team_id": "T1",
            "event_id": "Ev1",
            "event": {"type": "message", "channel": "C1", "text": "hello"},
        }
    ).encode()
    return client.post(
        "/webhooks/slack",
        content=body,
        headers={
            "content-type": "application/json",
            "x-slack-request-timestamp": "1780000000",
            "x-slack-signature": "v0="
            + hmac.new(SECRET.encode(), b"v0:1780000000:" + body, hashlib.sha256).hexdigest(),
        },
    )


def test_slack_durable_pipeline_keeps_context_and_responder_replay(tmp_path):
    class FailOnce(SqliteQueue):
        failed = False

        async def admit(self, jobs):
            # Capacity failures do not invalidate a storage connection.
            from anyo.execution import CapacityError

            if not self.failed:
                self.failed = True
                raise CapacityError
            return await super().admit(jobs)

    responses, calls = [], []

    async def responder(event):
        responses.append(event)
        return HookResponse()

    app = application(
        tmp_path, providers=[SlackProvider(clock=lambda: 1780000000)], backends={"sqlite": FailOnce}
    )
    app.add_hook(
        provider="slack",
        action="event",
        event_type="message",
        app_id="A1",
        team_id="T1",
        pipeline=calls.append,
        responder=responder,
        hook_id="slack",
        secret=SECRET,
    )
    with TestClient(app.asgi()) as client:
        assert slack_post(client).status_code == 503
        assert slack_post(client).status_code == 200
        client.portal.call(client.app.state.dispatcher.wait_idle)
    assert len(responses) == len(calls) == 1
    assert type(calls[0]) is SlackEvent
    assert calls[0].channel_id == "C1"


def test_async_queue_wait_respects_slack_deadline(tmp_path):
    class Waiting(SqliteQueue):
        async def admit(self, jobs):
            await asyncio.Event().wait()

    app = application(
        tmp_path,
        providers=[SlackProvider(clock=lambda: 1780000000, request_timeout=0.05)],
        backends={"sqlite": Waiting},
    )
    calls = []
    app.add_hook(
        provider="slack",
        action="event",
        event_type="message",
        app_id="A1",
        team_id="T1",
        pipeline=calls.append,
        hook_id="slack",
        secret=SECRET,
    )
    with TestClient(app.asgi(role="receiver")) as client:
        assert slack_post(client).status_code == 503
        assert client.portal.call(client.app.state.dispatcher.inspect) == []
    assert calls == []


def test_storage_error_is_safe_retryable_http_response(tmp_path):
    class Unavailable(SqliteQueue):
        failed = False

        async def admit(self, jobs):
            accepted = await super().admit(jobs)
            if not self.failed:
                self.failed = True
                raise QueueUnavailable("private storage details")
            return accepted

    app = application(tmp_path, backends={"sqlite": Unavailable})
    add(app, lambda event: None)
    with TestClient(app.asgi(role="receiver")) as client:
        response = post(client)
        assert response.status_code == 503
        assert "private" not in response.text
        assert post(client).json() == {"accepted": 0}
        assert post(client, "another").json() == {"accepted": 1}
        assert len(client.portal.call(client.app.state.dispatcher.inspect)) == 2
