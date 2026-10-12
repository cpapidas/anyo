"""Immediate-response ownership, concurrency, replay, and provider extension."""

import asyncio
from dataclasses import replace

import pytest
from starlette.testclient import TestClient

from anyo.application import Application
from anyo.execution import CapacityError, ResponseDispatcher
from anyo.hooks import Hook, HookEvent, HookResponse, WebhookDelivery, WebhookResponse


def hook(responder):
    return Hook("hook-1", "example", "ready", {}, None, responder=responder)


def event(identity="delivery-1"):
    return HookEvent("example", "ready", identity, payload={"nested": {"value": "original"}})


def render(result):
    return WebhookResponse(body=b"ok", dispatch=result.dispatch)


def test_concurrent_duplicates_share_one_responder_and_replay_result():
    async def run():
        entered, release, second_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        async def answer(value):
            calls.append(value)
            entered.set()
            await release.wait()
            return HookResponse(dispatch=False)

        registered = hook(answer)
        dispatcher = ResponseDispatcher(workers=1, max_records=10, retention_seconds=10)
        first = asyncio.create_task(dispatcher.respond(registered, event(), render))
        await asyncio.wait_for(entered.wait(), 2)

        async def duplicate():
            second_entered.set()
            return await dispatcher.respond(registered, event(), render)

        second = asyncio.create_task(duplicate())
        await asyncio.wait_for(second_entered.wait(), 2)
        release.set()
        a, b = await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert a == b == WebhookResponse(body=b"ok", dispatch=False)
        assert await dispatcher.respond(registered, event(), render) == a
        assert len(calls) == 1
        await dispatcher.close()

    asyncio.run(run())


def test_capacity_stays_reserved_until_cancelled_handler_finishes():
    async def run():
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def answer(value):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                raise

        registered = hook(answer)
        dispatcher = ResponseDispatcher(workers=1, max_records=10, retention_seconds=10)
        first = asyncio.create_task(dispatcher.respond(registered, event(), render))
        await asyncio.wait_for(entered.wait(), 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(cancelled.wait(), 2)
        with pytest.raises(CapacityError):
            await dispatcher.respond(registered, event("second"), render)
        release.set()
        await asyncio.wait_for(dispatcher.close(), 2)
        assert not any(t.get_name().startswith("anyo-response-") for t in asyncio.all_tasks())

    asyncio.run(run())


def test_response_history_capacity_and_retention_use_completion_time():
    async def run():
        clock = [0.0]
        calls = []

        async def answer(value):
            clock[0] += 10
            calls.append(value)
            return HookResponse()

        registered = hook(answer)
        dispatcher = ResponseDispatcher(
            workers=1, max_records=1, retention_seconds=5, clock=lambda: clock[0]
        )
        await dispatcher.respond(registered, event(), render)
        clock[0] = 14
        await dispatcher.respond(registered, event(), render)
        with pytest.raises(CapacityError):
            await dispatcher.respond(registered, event("next"), render)
        clock[0] = 15
        await dispatcher.respond(registered, event("next"), render)
        assert len(calls) == 2
        await dispatcher.close()

    asyncio.run(run())


def test_response_failure_allows_redelivery_and_does_not_mutate_original():
    async def run():
        attempts = []

        async def answer(value):
            assert value.payload["nested"]["value"] == "original"
            value.payload["nested"]["value"] = "changed"
            attempts.append(value)
            if len(attempts) == 1:
                raise ValueError("failure")
            return HookResponse()

        registered = hook(answer)
        dispatcher = ResponseDispatcher(workers=1, max_records=1, retention_seconds=5)
        original = event()
        with pytest.raises(ValueError):
            await dispatcher.respond(registered, original, render)
        assert (await dispatcher.respond(registered, original, render)).body == b"ok"
        assert original.payload["nested"]["value"] == "original"
        assert len(attempts) == 2
        await dispatcher.close()
        with pytest.raises(CapacityError):
            await dispatcher.respond(registered, event("next"), render)

    asyncio.run(run())


class ReplyProvider:
    name = "example"
    secret_env = "ANYO_EXAMPLE_SECRET"
    request_timeout = 2.5

    def validate(self, action, configuration):
        return {}

    def decode(self, body, headers, secret):
        if body == b"challenge":
            return WebhookDelivery(
                response=WebhookResponse(body=b"answer", media_type="text/plain", dispatch=False)
            )
        return WebhookDelivery(event(), render=render)

    def matches(self, configuration, value):
        return True


def test_a_consumer_provider_can_return_a_challenge_and_immediate_response():
    app = Application(providers=[ReplyProvider()])
    calls = []

    async def answer(value):
        calls.append("reply")
        return HookResponse()

    app.add_hook(
        provider="example",
        action="ready",
        secret="test",
        responder=answer,
        pipeline=lambda value: calls.append("pipeline"),
    )
    with TestClient(app.asgi()) as client:
        response = client.post("/webhooks/example", content=b"challenge")
        assert response.text == "answer"
        assert response.headers["content-type"].startswith("text/plain")
        assert calls == []
        assert client.post("/webhooks/example", content=b"event").content == b"ok"
    assert calls == ["reply", "pipeline"]


@pytest.mark.parametrize("supported", [False, True])
def test_consumer_provider_can_explicitly_control_responder_registration(supported):
    class ConfiguredProvider(ReplyProvider):
        supports_responders = supported

    app = Application(providers=[ConfiguredProvider()])

    async def answer(value):
        return HookResponse()

    options = dict(provider="example", action="ready", secret="test", responder=answer)
    if supported:
        app.add_hook(**options)
        with TestClient(app.asgi()) as client:
            assert client.post("/webhooks/example", content=b"event").content == b"ok"
    else:
        with pytest.raises(ValueError, match="does not support immediate responders"):
            app.add_hook(**options)
        # Opting out of responders preserves provider-owned acknowledgment support.
        app.add_hook(provider="example", action="ready", secret="test", pipeline=lambda e: None)
        with TestClient(app.asgi()) as client:
            assert client.post("/webhooks/example", content=b"challenge").content == b"answer"


@pytest.mark.parametrize("value", [None, 0, "false"])
def test_responder_capability_is_explicitly_boolean(value):
    class InvalidProvider(ReplyProvider):
        supports_responders = value

    with pytest.raises(TypeError, match="supports_responders must be a boolean"):
        Application(providers=[InvalidProvider()])


def test_expired_synchronous_matching_does_not_start_consumer_work(monkeypatch):
    class SlowMatchingProvider(ReplyProvider):
        def matches(self, configuration, value):
            loop = asyncio.get_running_loop()
            real_time = loop.time
            times = iter([real_time() + 10])
            monkeypatch.setattr(loop, "time", lambda: next(times, real_time()))
            return True

    calls = []

    async def answer(value):
        calls.append("reply")
        return HookResponse()

    app = Application(providers=[SlowMatchingProvider()])
    app.add_hook(
        provider="example",
        action="ready",
        secret="test",
        responder=answer,
        pipeline=lambda value: calls.append("pipeline"),
    )
    with TestClient(app.asgi()) as client:
        result = client.post("/webhooks/example", content=b"event")
        assert result.status_code == 503
        assert "deadline" in result.json()["error"]
    assert calls == []


def test_response_replay_and_background_admission_are_separate():
    from anyo.execution import Dispatcher

    async def run():
        released, entered = asyncio.Event(), asyncio.Event()
        replies, pipelines = [], []

        async def slow(value):
            entered.set()
            await released.wait()

        async def answer(value):
            replies.append(value)
            return HookResponse()

        response_hook = replace(hook(answer), pipeline=pipelines.append)
        blocked = Hook("blocking", "example", "ready", {}, slow)
        ordinary = Dispatcher(workers=1, capacity=1)
        immediate = ResponseDispatcher(workers=1, max_records=10, retention_seconds=10)
        await ordinary.start()
        ordinary.submit([(blocked, event())])
        await asyncio.wait_for(entered.wait(), 2)
        reply = await immediate.respond(response_hook, event(), render)
        with pytest.raises(CapacityError):
            ordinary.submit([(response_hook, event())])
        released.set()
        await asyncio.wait_for(ordinary.wait_idle(), 2)
        assert await immediate.respond(response_hook, event(), render) == reply
        assert ordinary.submit([(response_hook, event())]) == 1
        await ordinary.close()
        await immediate.close()
        assert len(replies) == len(pipelines) == 1

    asyncio.run(run())
