"""Consumer behavior for inert harness configuration and sync/async clients."""

import asyncio
import json
import threading

import pytest

import anyo
from anyo.agents import (
    Harness,
    Model,
    PromptClient,
    PromptEvent,
    PromptFailed,
    PromptResult,
    PromptSuccess,
    _Progress,
    parse_result,
)
from anyo.application import Application
from anyo.integrations.opencode import opencode_harness
from anyo.prompts import decode_json, load_prompt


def envelope(data=None, **changes):
    value = {"type": "success", "data": data, "failure_code": None, "failure_message": None}
    value.update(changes)
    return value


@pytest.mark.parametrize("value", [{"comments": []}, [1, "yes"], False, 0, "result", None])
def test_success_keeps_json_shape(value):
    result = parse_result(envelope(value), "run", "session", "message")
    assert result.failure is None
    assert result.success.data == value
    result.raise_for_failure()


def test_agent_failure_and_explicit_raise():
    result = parse_result(
        envelope(
            ["partial"], type="failure", failure_code="unavailable", failure_message="Missing diff"
        ),
        "r",
        "s",
        "m",
    )
    assert result.success is None
    assert result.failure.code == "unavailable"
    assert result.failure.data == ["partial"]
    assert result.failure.origin == "agent"
    with pytest.raises(PromptFailed) as raised:
        result.raise_for_failure()
    assert raised.value.result is result


@pytest.mark.parametrize(
    "value",
    [
        {},
        "```json\n{}\n```",
        envelope(failure_code="bad"),
        envelope(type="failure"),
        envelope(type="failure", failure_code="anyo.timeout", failure_message="attempt"),
        envelope(type="other"),
        {**envelope(), "extra": 1},
        envelope(float("nan")),
    ],
)
def test_rejects_invalid_envelopes(value):
    with pytest.raises(ValueError):
        parse_result(value, "r", "s", "m")


def test_json_boundary_and_loading(tmp_path):
    path = tmp_path / "prompt.md"
    path.write_text("Review")
    assert load_prompt(path, {"pr": 1}) == 'Review\n\nSupplied context (JSON data):\n{"pr": 1}'
    with pytest.raises(ValueError):
        decode_json('{"type":"success","type":"failure"}')
    with pytest.raises(ValueError):
        decode_json("[" * 70 + "null" + "]" * 70)
    with pytest.raises(FileNotFoundError):
        load_prompt(tmp_path / "missing")
    path.write_bytes(b"a" * 1_048_577)
    with pytest.raises(ValueError):
        load_prompt(path)


def test_harness_snapshot_repr_and_validation():
    models = [{"model": "anthropic/test", "key": "secret-sentinel"}]
    plugins = ["example-plugin@1.0.0"]
    harness = opencode_harness(models=models, plugins=plugins)
    models[0]["key"] = "changed"
    plugins.clear()
    assert harness.models[0].key == "secret-sentinel"
    assert harness.plugins == ("example-plugin@1.0.0",)
    assert "secret-sentinel" not in repr(harness)
    for invalid in [
        [],
        [{"model": "invalid", "key": "x"}],
        [{"model": "p/m", "key": ""}],
        [{"model": "p/m", "key": "a"}, {"model": "p/n", "key": "b"}],
    ]:
        with pytest.raises(ValueError):
            opencode_harness(models=invalid)
    with pytest.raises(ValueError):
        opencode_harness(models=[{"model": "p/m", "key": "k"}], max_concurrency=0)
    with pytest.raises(TypeError):
        opencode_harness(models=[{"model": "p/m", "key": "k"}], unsupported=True)


def test_progress_overflow_reports_loss_and_preserves_latest():
    progress = _Progress()
    for i in range(400):
        progress.put(PromptEvent("run", "status", str(i)))
    assert progress.pop().kind == "progress_gap"
    events = []
    while (event := progress.pop()) is not None:
        events.append(event)
    assert len(events) <= 256
    assert events[-1].message == "399"
    assert [event.sequence for event in events] == sorted(event.sequence for event in events)


class Backend:
    def __init__(self):
        self.requests = []
        self.closed = False

    async def invoke(self, request, emit):
        self.requests.append(request)
        emit(PromptEvent(request.run_id, "status", "working"))
        return PromptResult(request.run_id, PromptSuccess({"comments": []}))

    async def close(self):
        self.closed = True


def fake_harness(backend):
    return Harness("fake", (Model("p/a", "k"), Model("p/b", "k")), (), lambda: backend)


def test_standalone_sync_callbacks_on_caller_and_cleanup(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review")
    backend = Backend()
    events = []
    caller = threading.get_ident()

    def callback(event):
        assert threading.get_ident() == caller
        events.append(event.kind)

    with PromptClient(harness=fake_harness(backend)) as client:
        result = client.inject_prompt(prompt, workspace=tmp_path, model="p/b", on_event=callback)
        assert result.success.data == {"comments": []}
        with pytest.raises(ValueError):
            client.inject_prompt(prompt, model="p/missing")
    assert events == ["status", "terminal"]
    assert backend.requests[0].model == "p/b"
    assert backend.closed
    assert not client._thread.is_alive()


def test_async_observers_errors_and_slow_observers_do_not_change_result(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review")

    async def exercise():
        backend = Backend()
        async with PromptClient(harness=fake_harness(backend)) as client:

            async def broken(event):
                raise ValueError("secret-from-user")

            async def slow(event):
                await asyncio.Event().wait()

            for callback in [broken, slow]:
                result = await client.ainject_prompt(prompt, on_event=callback)
                assert result.success is not None
            with pytest.raises(RuntimeError, match="ainject_prompt"):
                client.inject_prompt(prompt)
        assert backend.closed

    asyncio.run(exercise())


def test_application_harness_registration_is_inert():
    app = Application(harnesses={"opencode": opencode_harness})
    handle = app.harness(provider="opencode", models=[{"model": "p/a", "key": "k"}])
    assert handle.provider == "opencode"
    with pytest.raises(RuntimeError):
        app.harness(provider="opencode", models=[])
    app.reset_harness()
    app.harness(provider="opencode", models=[{"model": "p/b", "key": "k"}])
    with pytest.raises(RuntimeError, match="PromptClient"):
        anyo.inject_prompt("unused")


def test_application_sync_and_async_hooks_receive_their_own_runtime(tmp_path):
    import hashlib
    import hmac

    from starlette.testclient import TestClient

    from anyo.integrations.github import GitHubProvider

    path = tmp_path / "prompt.md"
    path.write_text("Review")
    outcomes = []
    backend = Backend()
    app = Application(
        providers=[GitHubProvider()], harnesses={"fake": lambda **kwargs: fake_harness(backend)}
    )
    app.harness(provider="fake", models=[])

    def sync_hook(event):
        outcomes.append(anyo.inject_prompt(path))

    async def async_hook(event):
        outcomes.append(await anyo.ainject_prompt(path))

    for pipeline in [sync_hook, async_hook]:
        app.add_hook(
            provider="github",
            action="pr_created",
            organization="org",
            repository="repo",
            secret="secret",
            pipeline=pipeline,
        )
    body = json.dumps(
        {
            "action": "opened",
            "number": 1,
            "repository": {"name": "repo", "owner": {"login": "org"}},
            "pull_request": {"head": {"sha": "a" * 40}},
        }
    ).encode()
    with TestClient(app.asgi()) as http:
        assert (
            http.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-github-event": "pull_request",
                    "x-github-delivery": "one",
                    "x-hub-signature-256": "sha256="
                    + hmac.new(b"secret", body, hashlib.sha256).hexdigest(),
                },
            ).status_code
            == 202
        )
        with pytest.raises(RuntimeError):
            app.reset_harness()
    assert len(outcomes) == 2
    assert backend.closed
