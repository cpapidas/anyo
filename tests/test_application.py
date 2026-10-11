"""Consumer behavior through registration and the real webhook receiver."""

import asyncio
import hashlib
import hmac
import json
import logging
import socket
import threading
from collections.abc import Mapping

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

import anyo
from anyo.application import Application
from anyo.hooks import HookEvent, WebhookError
from anyo.integrations.github import GitHubProvider

SECRET = "test-only-secret"


def payload(repository="repo", owner="orga", action="opened"):
    return {
        "action": action,
        "number": 42,
        "repository": {"name": repository, "owner": {"login": owner}},
        "pull_request": {
            "head": {"sha": "a" * 40, "repo": {"name": "fork", "owner": {"login": "forker"}}}
        },
    }


def delivery(client, data=None, *, secret=SECRET, identity="delivery-1", event="pull_request"):
    body = json.dumps(payload() if data is None else data).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "content-type": "application/json",
            "x-github-delivery": identity,
            "x-github-event": event,
            "x-hub-signature-256": "sha256="
            + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
        },
    )


def application(callback, **kwargs):
    app = Application(providers=[GitHubProvider()])
    add(app, callback, **kwargs)
    return app


def add(app, callback, **kwargs):
    options = {"organization": "orga", "repository": "repo", "secret": SECRET}
    options.update(kwargs)
    return app.add_hook(provider="github", action="pr_created", pipeline=callback, **options)


def test_module_api_is_registered_without_operational_side_effects():
    # Import does not require a configured secret or a running event loop.
    assert callable(anyo.add_hook)
    assert callable(anyo.start)
    assert anyo.HookEvent is HookEvent


def test_real_signed_event_fans_out_and_uses_target_repository():
    first, second, other = [], [], []
    app = application(first.append, organization="ORGA", repository="REPO")
    add(app, second.append)
    add(app, other.append, repository="another")
    with TestClient(app.asgi()) as client:
        response = delivery(client)
        assert response.status_code == 202
        assert response.json() == {"accepted": 2}
        assert delivery(client).json() == {"accepted": 0}
    assert len(first) == len(second) == 1
    assert other == []
    event = first[0]
    assert event.delivery_id == "delivery-1"
    assert event.organization == "orga"
    assert event.repository == "repo"
    assert event.pull_request_number == 42
    assert event.head_sha == "a" * 40
    assert first[0].payload is not second[0].payload


def test_async_callback_is_awaited():
    calls = []

    async def callback(event):
        calls.append(event)

    app = application(callback)
    with TestClient(app.asgi()) as client:
        assert delivery(client).status_code == 202
    assert len(calls) == 1


def test_each_secret_only_authorizes_its_own_hooks():
    first, second = [], []
    app = application(first.append)
    add(app, second.append, secret="different-secret")
    with TestClient(app.asgi()) as client:
        assert delivery(client).json() == {"accepted": 1}
        assert delivery(client, secret="different-secret").json() == {"accepted": 1}
        assert delivery(client, secret="invalid").status_code == 401
    assert len(first) == len(second) == 1


def test_valid_secret_for_other_repository_cannot_dispatch():
    calls = []
    app = application(calls.append)
    add(app, lambda event: None, secret="other-secret", repository="other")
    with TestClient(app.asgi()) as client:
        assert delivery(client, secret="other-secret").json() == {"accepted": 0}
    assert calls == []


@pytest.mark.parametrize(
    "data,event,expected",
    [
        ({}, "ping", 200),
        ({}, "push", 200),
        (payload(action="closed"), "pull_request", 200),
        (payload(repository="other"), "pull_request", 200),
        (payload(owner="another"), "pull_request", 200),
        ({}, "pull_request", 400),
    ],
)
def test_irrelevant_and_malformed_events_do_not_run(data, event, expected):
    calls = []
    with TestClient(application(calls.append).asgi()) as client:
        assert delivery(client, data, event=event).status_code == expected
    assert not calls


def test_request_rejections_and_methods():
    calls = []
    with TestClient(application(calls.append).asgi(max_body_bytes=512)) as client:
        assert client.get("/webhooks/github").status_code == 405
        assert client.post("/webhooks/unknown").status_code == 404
        assert client.post("/webhooks/github", content=b"{}").status_code == 401
        assert client.post("/webhooks/github", content=b"x" * 513).status_code == 413
        assert (
            client.post(
                "/webhooks/github", content=b"{}", headers={"content-length": "bad"}
            ).status_code
            == 400
        )
    assert not calls


def test_slow_callback_does_not_block_response_and_capacity_is_atomic():
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow(event):
        started.set()
        assert release.wait(5)
        calls.append(event)

    app = application(slow)
    add(app, lambda event: calls.append(event))
    with TestClient(app.asgi(workers=1, capacity=2)) as client:
        try:
            # The first callback is still blocked when the HTTP response arrives.
            assert delivery(client).json() == {"accepted": 2}
            assert started.wait(5)
            assert not calls
            assert delivery(client, identity="delivery-2").status_code == 503
            assert delivery(client).json() == {"accepted": 0}
        finally:
            release.set()
    assert len(calls) == 2


def test_group_larger_than_capacity_runs_nothing():
    calls = []
    app = application(calls.append)
    add(app, lambda event: calls.append(event))
    with TestClient(app.asgi(capacity=1)) as client:
        assert delivery(client).status_code == 503
    assert calls == []


def test_environment_secret_is_resolved_at_startup(monkeypatch):
    monkeypatch.delenv("ANYO_GITHUB_WEBHOOK_SECRET", raising=False)
    calls = []
    app = application(calls.append, secret=None)
    with pytest.raises(ValueError, match="ANYO_GITHUB_WEBHOOK_SECRET"):
        with TestClient(app.asgi()):
            pass
    monkeypatch.setenv("ANYO_GITHUB_WEBHOOK_SECRET", SECRET)
    with TestClient(app.asgi()) as client:
        assert delivery(client).status_code == 202
    assert len(calls) == 1


def test_registration_errors():
    app = Application(providers=[GitHubProvider()])

    def callback(event):
        pass

    hook = add(app, callback)
    assert SECRET not in repr(hook)
    with pytest.raises(TypeError):
        hook.configuration["repository"] = "mutated"
    with pytest.raises(ValueError, match="already registered"):
        add(app, callback)
    for callback in [None, lambda: None, lambda a, b: None]:
        with pytest.raises(TypeError, match="pipeline"):
            add(app, callback)
    for changes in [{"secret": ""}, {"secret": 5}, {"unexpected": "x"}]:
        with pytest.raises(ValueError):
            add(app, lambda event: None, **changes)
    with pytest.raises(ValueError, match="Unknown trigger"):
        app.add_hook(provider="unknown", action="pr_created", pipeline=lambda event: None)
    with pytest.raises(ValueError, match="pr_created"):
        app.add_hook(provider="github", action="other", pipeline=lambda event: None)


def test_lifecycle_freezes_registry_and_allows_sequential_starts():
    calls = []
    app = application(calls.append)
    asgi = app.asgi()
    with TestClient(asgi) as client:
        with pytest.raises(RuntimeError, match="running"):
            add(app, lambda event: None)
        with pytest.raises(RuntimeError, match="already running"):
            with TestClient(app.asgi()):
                pass
        assert delivery(client).status_code == 202
    with TestClient(asgi) as client:
        # History intentionally resets with each lifespan.
        assert delivery(client).status_code == 202
    assert len(calls) == 2
    add(app, lambda event: None)


def test_empty_start_and_bind_errors_release_claim():
    with pytest.raises(ValueError, match="at least one"):
        Application().start(port=0)
    app = application(lambda event: None)
    with socket.create_server(("127.0.0.1", 0)) as listener:
        with pytest.raises(OSError):
            app.start(port=listener.getsockname()[1])
    with TestClient(app.asgi()) as client:
        assert delivery(client).status_code == 202


def test_start_rejects_existing_event_loop():
    async def check():
        with pytest.raises(RuntimeError, match="event loop"):
            application(lambda event: None).start(port=0)

    asyncio.run(check())


@pytest.mark.parametrize(
    "options",
    [
        {"max_body_bytes": 0},
        {"max_body_bytes": True},
        {"request_timeout": 0},
        {"request_timeout": float("inf")},
        {"workers": 0},
        {"capacity": False},
        {"max_records": -1},
        {"retention_seconds": float("nan")},
    ],
)
def test_invalid_runtime_options(options):
    with pytest.raises(ValueError):
        application(lambda event: None).asgi(**options)


def test_import_and_start_do_not_change_root_logging(monkeypatch):
    handlers = logging.getLogger().handlers[:]
    app = application(lambda event: None)

    def run(server, sockets):
        assert sockets[0].getsockname()[1] > 0
        server.started = True

    monkeypatch.setattr("uvicorn.Server.run", run)
    app.start(port=0)
    assert logging.getLogger().handlers == handlers


class FakeProvider:
    name = "fake"
    secret_env = "ANYO_FAKE_SECRET"

    def validate(self, action: str, configuration: Mapping[str, object]) -> Mapping[str, str]:
        if action != "ready" or configuration:
            raise ValueError("Unsupported fake registration")
        return {}

    def decode(self, body: bytes, headers: Mapping[str, str], secret: str) -> HookEvent:
        if headers.get("authorization") != secret:
            raise WebhookError(401, "Invalid fake signature")
        return HookEvent("fake", "ready", "fake-1", "", "", {"text": body.decode()})

    def matches(self, configuration: Mapping[str, str], event: HookEvent) -> bool:
        return event.action == "ready"


def test_another_provider_uses_the_same_runtime_without_github_changes():
    calls = []
    app = Application(providers=[FakeProvider()])
    app.add_hook(provider="fake", action="ready", pipeline=calls.append, secret="fake-secret")
    with TestClient(app.asgi()) as client:
        response = client.post(
            "/webhooks/fake", content=b"ready", headers={"authorization": "fake-secret"}
        )
        assert response.status_code == 202
    assert len(calls) == 1
    assert calls[0].provider == "fake"


def test_body_limit_applies_to_streams_without_content_length():
    app = application(lambda event: None)

    async def run():
        asgi = app.asgi(max_body_bytes=4)
        messages = iter(
            [
                {"type": "http.request", "body": b"123", "more_body": True},
                {"type": "http.request", "body": b"45", "more_body": False},
            ]
        )
        responses = []

        async def receive():
            return next(messages)

        async def send(message):
            responses.append(message)

        async with asgi.router.lifespan_context(asgi):
            await asgi(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/webhooks/github",
                    "headers": [],
                    "query_string": b"",
                    "scheme": "http",
                    "server": ("test", 80),
                },
                receive,
                send,
            )
        assert responses[0]["status"] == 413

    asyncio.run(run())


def test_body_timeout_response_without_a_wall_clock_sleep(monkeypatch):
    # Raise at the stream boundary to exercise the real timeout translation.
    async def timed_out(self):
        raise TimeoutError
        yield b""  # Keep the same async-generator protocol as Request.stream.

    monkeypatch.setattr(Request, "stream", timed_out)
    with TestClient(application(lambda event: None).asgi()) as client:
        assert client.post("/webhooks/github", content=b"{}").status_code == 408


def test_asgi_without_lifespan_fails_closed():
    app = application(lambda event: None)
    client = TestClient(app.asgi())
    try:
        assert delivery(client).status_code == 503
    finally:
        client.close()


def test_deep_payload_is_rejected_before_any_callback_runs():
    calls = []
    data = payload()
    nested = []
    for _ in range(600):
        nested = [nested]
    data["extra"] = nested
    with TestClient(application(calls.append).asgi()) as client:
        assert delivery(client, data).status_code == 400
        # Rejection must not reserve the delivery ID or any queue capacity.
        assert delivery(client).status_code == 202
    assert len(calls) == 1
