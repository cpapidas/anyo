"""Signed, local Slack deliveries shared by adapter and HTTP contract tests."""

import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from anyo.application import Application
from anyo.integrations.slack import SlackProvider

SECRET = "local-slack-test-secret"
NOW = 1780000000


def event_payload(event_type="message", **fields):
    return {
        "type": "event_callback",
        "api_app_id": "A123",
        "team_id": "T123",
        "event_id": "Ev123",
        "event": {"type": event_type, **fields},
    }


def interaction_payload(event_type="block_actions", **fields):
    data = {
        "type": event_type,
        "api_app_id": "A123",
        "team": {"id": "T123"},
        "user": {"id": "U123"},
        "trigger_id": "invocation-1",
    }
    if event_type == "block_actions":
        data["actions"] = [
            {
                "action_id": "approve",
                "block_id": "review",
                "type": "button",
                "action_ts": "1780000000.001",
            }
        ]
    elif event_type in {"shortcut", "message_action", "dialog_submission", "dialog_cancellation"}:
        data["callback_id"] = "review"
    elif event_type in {"view_submission", "view_closed"}:
        data["view"] = {
            "id": "V123",
            "type": "modal",
            "callback_id": "review",
            "state": {"values": {}},
        }
    elif event_type == "block_suggestion":
        data.update(action_id="search", block_id="review", value="hello")
    elif event_type == "dialog_suggestion":
        data.update(name="search", callback_id="review", value="hello")
    data.update(fields)
    return data


def command_payload(**fields):
    return {
        "api_app_id": "A123",
        "team_id": "T123",
        "channel_id": "C123",
        "user_id": "U123",
        "command": "/anyo",
        "text": "hello",
        "trigger_id": "invocation-1",
        **fields,
    }


def encoded(data, kind="json"):
    if kind == "json":
        return json.dumps(data, ensure_ascii=False).encode(), "application/json"
    if kind == "interaction":
        return urlencode(
            {"payload": json.dumps(data, ensure_ascii=False)}
        ).encode(), "application/x-www-form-urlencoded"
    return urlencode(data).encode(), "application/x-www-form-urlencoded"


def signed(body, content_type="application/json", *, secret=SECRET, timestamp=NOW, **extra):
    headers = {
        "content-type": content_type,
        "x-slack-request-timestamp": str(timestamp),
        "x-slack-signature": "v0="
        + hmac.new(
            secret.encode(), b"v0:" + str(timestamp).encode() + b":" + body, hashlib.sha256
        ).hexdigest(),
    }
    headers.update(extra)
    return headers


@pytest.fixture
def provider():
    return SlackProvider(clock=lambda: NOW)


@pytest.fixture
def app(provider):
    return Application(providers=[provider])


def add(app, pipeline=None, *, action="event", responder=None, **configuration):
    options = {"app_id": "A123", "team_id": "T123", "secret": SECRET}
    options.update(configuration)
    return app.add_hook(
        provider="slack", action=action, pipeline=pipeline, responder=responder, **options
    )


def deliver(client, data=None, *, kind="json", timestamp=NOW, secret=SECRET, **extra):
    body, content_type = encoded(event_payload() if data is None else data, kind)
    return client.post(
        "/webhooks/slack",
        content=body,
        headers=signed(body, content_type, secret=secret, timestamp=timestamp, **extra),
    )
