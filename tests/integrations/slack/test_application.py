"""Slack callbacks through the real HTTP receiver and shared runtime."""

import asyncio
import hashlib
import hmac
import json
import threading
from contextlib import asynccontextmanager

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount
from starlette.testclient import TestClient

from anyo import HookResponse, lifespan
from anyo.application import Application
from anyo.integrations.github import GitHubProvider
from anyo.integrations.slack import SlackEvent, SlackProvider

from .conftest import (
    NOW,
    SECRET,
    add,
    command_payload,
    deliver,
    encoded,
    event_payload,
    interaction_payload,
    signed,
)


def test_mounted_slack_responder_preserves_reply_and_duplicate_replay(app):
    immediate, pipelines = [], []

    async def answer(event):
        immediate.append(event)
        return HookResponse({"text": "Accepted by the mounted app"}, dispatch=False)

    add(app, pipelines.append, action="slash_command", command="/anyo", responder=answer)
    child = app.asgi()

    @asynccontextmanager
    async def host_lifespan(host):
        async with lifespan(child):
            yield

    host = Starlette(routes=[Mount("/anyo", child)], lifespan=host_lifespan)
    body, content_type = encoded(command_payload(), "form")
    with TestClient(host) as client:
        for _ in range(2):
            response = client.post(
                "/anyo/webhooks/slack", content=body, headers=signed(body, content_type)
            )
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/json"
            assert response.json() == {"text": "Accepted by the mounted app"}
    assert len(immediate) == 1 and not pipelines


def test_events_fan_out_dedupe_and_copy_payloads(app):
    first, second, excluded = [], [], []

    def change(event):
        event.payload["event"]["text"] = "changed"
        first.append(event)

    add(app, change, event_type="message")
    add(app, second.append, event_type="*")
    add(app, excluded.append, team_id="TOTHER")
    with TestClient(app.asgi()) as client:
        one = deliver(client, event_payload(text="original"))
        assert one.status_code == 200 and one.content == b""
        assert deliver(client, event_payload(text="original")).status_code == 200
    assert len(first) == len(second) == 1
    assert second[0].payload["event"]["text"] == "original"
    assert isinstance(second[0], SlackEvent)
    assert not excluded


def test_environment_secret_startup_and_per_hook_auth(app, monkeypatch):
    first, second = [], []
    add(app, first.append, secret=None)
    add(app, second.append, secret="different")
    monkeypatch.delenv("ANYO_SLACK_SIGNING_SECRET", raising=False)
    with pytest.raises(ValueError, match="ANYO_SLACK_SIGNING_SECRET"):
        with TestClient(app.asgi()):
            pass
    monkeypatch.setenv("ANYO_SLACK_SIGNING_SECRET", SECRET)
    with TestClient(app.asgi()) as client:
        assert deliver(client).status_code == 200
        assert deliver(client, secret="different").status_code == 200
        assert deliver(client, secret="wrong").status_code == 401
    assert len(first) == len(second) == 1


def test_setup_and_security_never_call_pipeline(app):
    calls = []
    add(app, calls.append)
    with TestClient(app.asgi()) as client:
        response = deliver(client, {"type": "url_verification", "challenge": "verify-me"})
        assert response.json() == {"challenge": "verify-me"}
        assert deliver(client, {"ssl_check": "1"}, kind="form").content == b""
        assert deliver(client, timestamp=NOW - 301).status_code == 401
        assert client.post("/webhooks/slack", content=b"{}").status_code == 401
        assert (
            deliver(
                client, {"type": "future_control", "api_app_id": "A123", "team_id": "T123"}
            ).status_code
            == 200
        )
    assert not calls


def test_commands_ack_and_separate_real_invocations(app):
    calls = []
    add(app, calls.append, action="slash_command", command="/anyo")
    with TestClient(app.asgi()) as client:
        assert deliver(client, command_payload(), kind="form").content == b""
        assert deliver(client, command_payload(), kind="form", timestamp=NOW + 1).status_code == 200
        assert deliver(client, command_payload(trigger_id="second"), kind="form").status_code == 200
    assert len(calls) == 2


def test_multiple_action_matches_schedule_each_hook_once(app):
    a, b = [], []
    add(app, a.append, action="interaction", action_id="approve")
    add(app, b.append, action="interaction", action_id="reject")
    data = interaction_payload(
        actions=[{"action_id": "approve"}, {"action_id": "approve"}, {"action_id": "reject"}]
    )
    with TestClient(app.asgi()) as client:
        assert deliver(client, data, kind="interaction").status_code == 200
    assert len(a) == len(b) == 1
    assert len(a[0].actions) == 3


def test_modal_response_owns_acceptance_and_replays_on_retry(app):
    immediate, pipelines = [], []

    async def validate(event):
        immediate.append(event)
        return HookResponse({"response_action": "errors", "errors": {"email": "Invalid email"}})

    add(
        app,
        pipelines.append,
        action="interaction",
        interaction_type="view_submission",
        responder=validate,
    )
    add(app, lambda event: pipelines.append(event), action="interaction", interaction_type="*")
    with TestClient(app.asgi()) as client:
        data = interaction_payload("view_submission")
        a = deliver(client, data, kind="interaction")
        b = deliver(client, data, kind="interaction", **{"x-slack-retry-num": "1"})
        assert a.status_code == b.status_code == 200
        assert (
            a.json()
            == b.json()
            == {"response_action": "errors", "errors": {"email": "Invalid email"}}
        )
    assert len(immediate) == 1
    assert not pipelines


def test_accepted_modal_and_private_responder_copy(app):
    pipelines = []

    async def accept(event):
        event.payload["view"]["callback_id"] = "mutated"
        return HookResponse()

    add(
        app,
        pipelines.append,
        action="interaction",
        interaction_type="view_submission",
        responder=accept,
    )
    with TestClient(app.asgi()) as client:
        response = deliver(client, interaction_payload("view_submission"), kind="interaction")
        assert response.status_code == 200 and response.content == b""
    assert pipelines[0].payload["view"]["callback_id"] == "review"


@pytest.mark.parametrize("action", ["push", "update", "clear"])
def test_modal_view_responses(app, action):
    async def answer(event):
        body = {"response_action": action}
        if action != "clear":
            body["view"] = {
                "type": "modal",
                "title": {"type": "plain_text", "text": "Next"},
                "blocks": [],
            }
        return HookResponse(body, dispatch=False)

    add(app, responder=answer, action="interaction", interaction_type="view_submission")
    with TestClient(app.asgi()) as client:
        response = deliver(client, interaction_payload("view_submission"), kind="interaction")
        assert response.status_code == 200
        assert response.json()["response_action"] == action


@pytest.mark.parametrize(
    "event_type", ["block_suggestion", "dialog_suggestion", "interactive_message"]
)
def test_dynamic_menu_response_and_required_responder(app, event_type):
    async def answer(event):
        return HookResponse(
            {
                "options": [
                    {
                        "text": {"type": "plain_text", "text": "One"}
                        if event_type == "block_suggestion"
                        else "One",
                        "value": "one",
                    }
                ]
            },
            dispatch=False,
        )

    data = interaction_payload(event_type, name="search", value="o")
    # A mismatched hook demonstrates that an options load cannot silently ack.
    add(app, lambda event: None, action="event")
    with TestClient(app.asgi()) as client:
        assert deliver(client, data, kind="interaction").status_code == 503
    add(
        app,
        responder=answer,
        action="interaction",
        interaction_type=event_type,
        options_request=True,
    )
    with TestClient(app.asgi()) as client:
        result = deliver(client, data, kind="interaction")
        assert result.status_code == 200
        assert result.json()["options"][0]["value"] == "one"


def test_legacy_dialog_validation_suppresses_work(app):
    calls = []

    async def answer(event):
        return HookResponse({"errors": [{"name": "email", "error": "Invalid"}]})

    add(
        app,
        calls.append,
        action="interaction",
        responder=answer,
        interaction_type="dialog_submission",
    )
    with TestClient(app.asgi()) as client:
        result = deliver(client, interaction_payload("dialog_submission"), kind="interaction")
        assert result.status_code == 200
        assert result.json()["errors"]
    assert not calls


def test_command_immediate_reply_and_suppression(app):
    calls = []

    async def answer(event):
        return HookResponse({"text": "Ready", "response_type": "ephemeral"}, dispatch=False)

    add(app, calls.append, responder=answer, action="slash_command")
    with TestClient(app.asgi()) as client:
        result = deliver(client, command_payload(), kind="form")
        assert result.json() == {"text": "Ready", "response_type": "ephemeral"}
    assert not calls


@pytest.mark.parametrize(
    "result",
    [
        None,
        {"options": []},
        HookResponse({"options": "wrong"}),
        HookResponse({"options": [{"text": "plain string invalid for Block Kit", "value": "x"}]}),
        HookResponse({"options": []}, dispatch="yes"),
    ],
)
def test_invalid_responder_result_never_dispatches(app, result, caplog):
    calls = []

    async def answer(event):
        return result

    add(app, calls.append, responder=answer, action="interaction")
    with TestClient(app.asgi()) as client:
        response = deliver(client, interaction_payload("block_suggestion"), kind="interaction")
        assert response.status_code == 503
        assert response.json() == {"error": "Immediate responder failed"}
    assert not calls
    assert SECRET not in caplog.text


def test_conflicting_responders_fail_before_consumer_code(app):
    calls = []

    async def first(event):
        calls.append(event)
        return HookResponse()

    async def second(event):
        calls.append(event)
        return HookResponse()

    add(app, responder=first, action="interaction", interaction_type="block_actions")
    add(app, responder=second, action="interaction", interaction_type="*")
    with TestClient(app.asgi()) as client:
        assert deliver(client, interaction_payload(), kind="interaction").status_code == 409
    assert not calls


def test_slow_background_does_not_block_options_and_capacity_is_atomic(app):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow(event):
        entered.set()
        assert release.wait(5)
        calls.append(event)

    async def options(event):
        return HookResponse({"options": []}, dispatch=False)

    add(app, slow)
    add(app, responder=options, action="interaction", interaction_type="block_suggestion")
    with TestClient(app.asgi(workers=1, capacity=1)) as client:
        try:
            assert deliver(client).status_code == 200
            assert entered.wait(5)
            assert deliver(
                client, interaction_payload("block_suggestion"), kind="interaction"
            ).json() == {"options": []}
            other = event_payload()
            other["event_id"] = "EvOther"
            assert deliver(client, other).status_code == 503
            assert not calls
        finally:
            release.set()
    assert len(calls) == 1


def test_pipeline_redelivery_reruns_only_failed_hook(app):
    attempts, good = [], []
    completed = threading.Event()

    def failing(event):
        attempts.append(event)
        if len(attempts) == 1:
            raise RuntimeError("private exception")

    def success(event):
        good.append(event)
        completed.set()

    add(app, failing)
    add(app, success)
    with TestClient(app.asgi(workers=1)) as client:
        assert deliver(client).status_code == 200
        assert completed.wait(5)
        assert deliver(client).status_code == 200
    assert len(attempts) == 2
    assert len(good) == 1


def test_restart_resets_both_pipeline_and_response_history(app):
    calls = []

    async def answer(event):
        calls.append("reply")
        return HookResponse()

    add(app, lambda event: calls.append("pipeline"), responder=answer, action="interaction")
    asgi = app.asgi()
    for _ in range(2):
        with TestClient(asgi) as client:
            assert deliver(client, interaction_payload(), kind="interaction").status_code == 200
    assert calls == ["reply", "pipeline", "reply", "pipeline"]


def test_github_and_slack_share_runtime_without_response_changes(provider):
    app = Application(providers=[GitHubProvider(), provider])
    github, slack = [], []
    app.add_hook(
        provider="github",
        action="pr_created",
        organization="org",
        repository="repo",
        pipeline=github.append,
        secret=SECRET,
    )
    add(app, slack.append)
    data = {
        "action": "opened",
        "number": 1,
        "repository": {"name": "repo", "owner": {"login": "org"}},
        "pull_request": {"head": {"sha": "a" * 40}},
    }
    raw = json.dumps(data).encode()
    with TestClient(app.asgi()) as client:
        result = client.post(
            "/webhooks/github",
            content=raw,
            headers={
                "x-github-delivery": "delivery-1",
                "x-github-event": "pull_request",
                "x-hub-signature-256": "sha256="
                + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest(),
            },
        )
        assert result.status_code == 202 and result.json() == {"accepted": 1}
        assert deliver(client).status_code == 200
    assert len(github) == len(slack) == 1


def test_responder_registration_validation(app):
    async def response(event):
        return HookResponse()

    add(app, responder=response)
    with pytest.raises(ValueError, match="already registered"):
        add(app, responder=response)
    with pytest.raises(TypeError, match="async"):
        add(app, responder=lambda event: HookResponse())
    github = Application(providers=[GitHubProvider()])
    with pytest.raises(ValueError, match="does not support"):
        github.add_hook(
            provider="github",
            action="pr_created",
            organization="org",
            repository="repo",
            responder=response,
        )


def test_total_budget_cancels_a_blocked_responder_without_dispatch():
    app = Application(providers=[SlackProvider(clock=lambda: NOW, request_timeout=0.02)])
    cancelled = threading.Event()
    calls = []

    async def response(event):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return HookResponse()

    add(app, calls.append, responder=response)
    with TestClient(app.asgi()) as client:
        result = deliver(client)
        assert result.status_code == 503
        assert "deadline" in result.json()["error"]
        assert cancelled.wait(5)
    assert not calls


def test_body_limits_and_deadlines_apply_to_slack(app, monkeypatch):
    calls = []
    add(app, calls.append)
    with TestClient(app.asgi(max_body_bytes=10)) as client:
        assert deliver(client).status_code == 413

    async def timed_out(self):
        raise TimeoutError
        yield b""

    monkeypatch.setattr(Request, "stream", timed_out)
    with TestClient(app.asgi()) as client:
        assert deliver(client).status_code == 408
    assert not calls


def test_completed_response_survives_background_overload(app):
    entered, release = threading.Event(), threading.Event()
    responder_calls, jobs = [], []

    def slow(event):
        entered.set()
        assert release.wait(5)

    async def answer(event):
        responder_calls.append(event)
        return HookResponse()

    add(app, slow, event_type="message")
    add(app, jobs.append, responder=answer, action="interaction")
    with TestClient(app.asgi(workers=1, capacity=1)) as client:
        try:
            assert deliver(client).status_code == 200
            assert entered.wait(5)
            data = interaction_payload()
            assert deliver(client, data, kind="interaction").status_code == 503
            # Still overloaded: response must already be cached.
            assert deliver(client, data, kind="interaction").status_code == 503
            assert len(responder_calls) == 1
        finally:
            release.set()

    assert not jobs


@pytest.mark.parametrize(
    "kind",
    [
        "block_actions",
        "shortcut",
        "message_action",
        "view_submission",
        "view_closed",
        "dialog_submission",
        "dialog_cancellation",
        "interactive_message",
    ],
)
def test_every_empty_interaction_ack_through_http(app, kind):
    calls = []
    add(app, calls.append, action="interaction", interaction_type=kind)
    data = interaction_payload(kind)
    if kind == "interactive_message":
        data["actions"] = [{"name": "approve", "type": "button"}]
    with TestClient(app.asgi()) as client:
        response = deliver(client, data, kind="interaction")
        assert response.status_code == 200 and response.content == b""
    assert len(calls) == 1
    assert calls[0].event_type == kind


def test_modern_and_legacy_option_groups(app):
    async def modern(event):
        return HookResponse(
            {
                "option_groups": [
                    {
                        "label": {"type": "plain_text", "text": "Group"},
                        "options": [
                            {"text": {"type": "plain_text", "text": "Option"}, "value": "one"}
                        ],
                    }
                ]
            }
        )

    async def legacy(event):
        return HookResponse(
            {"option_groups": [{"text": "Group", "options": [{"text": "Option", "value": "one"}]}]}
        )

    add(app, responder=modern, action="interaction", interaction_type="block_suggestion")
    add(app, responder=legacy, action="interaction", interaction_type="dialog_suggestion")
    with TestClient(app.asgi()) as client:
        for kind in ["block_suggestion", "dialog_suggestion"]:
            result = deliver(client, interaction_payload(kind), kind="interaction")
            assert result.status_code == 200
            assert len(result.json()["option_groups"]) == 1
